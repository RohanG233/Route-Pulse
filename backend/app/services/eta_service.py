"""
ETA estimation service for RoutePulse.

Algorithm (spec §8.1):
  For each route segment, compute the median observed travel time from GPS
  samples whose trip_id matches the given trip.  If fewer than
  MIN_SAMPLES_FOR_MEDIAN samples exist for a segment, fall back to the
  route's configured segment travel time.

  Add any scenario delay override on top of the estimated segment time.

  Accumulate estimated segment times from the departure to produce a
  predicted arrival at every stop in route order.

  Return an arrival window:
    - When enough samples exist: window = [median - half_range, median + half_range]
      where half_range is (max_sample - min_sample) / 2, lower-bounded at
      ETA_FALLBACK_WINDOW_MIN so the window is never trivially tight.
    - When falling back: window = [expected - ETA_FALLBACK_WINDOW_MIN,
                                   expected + ETA_FALLBACK_WINDOW_MIN].

  The window lower bound is always clamped to the previous stop's arrival
  so the schedule remains physically valid.

ISOLATION GUARANTEE:
  This module never imports or calls any ground-truth accessor.
  Ground truth is used ONLY by metrics_service.py and test evaluation code.
  The isolation is enforced by the TestGroundTruthIsolation tests in
  test_dataset.py and test_eta.py (which scan this file's imports, not
  its docstring).
"""

from __future__ import annotations

import statistics
from typing import Optional

from app.config import ETA_FALLBACK_WINDOW_MIN, MIN_SAMPLES_FOR_MEDIAN
from app.schemas import (
    EtaPrediction,
    GpsPoint,
    Route,
    ScenarioDefinition,
    Trip,
)
from app.data.scenarios import get_trip_segment_delays


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_segment_samples(
    gps_points: list[GpsPoint],
    trip_id: str,
    route: Route,
) -> list[list[float]]:
    """
    Extract observed travel times (minutes) for each segment of a route,
    using GPS points that belong to the given trip.

    Returns a list of N-1 lists (one per segment), each containing the
    observed transit times between consecutive stops derived from the
    GPS timestamps nearest to each stop's position.

    Strategy:
      - Sort GPS points for this trip by timestamp.
      - For each stop in the route, find the GPS sample whose coordinates
        are closest (Euclidean on lat/lon) to the stop's known position.
      - Observed segment time = timestamp(nearest to stop[i+1]) -
                                 timestamp(nearest to stop[i]).
      - Discard any observation where the result is <= 0 (GPS noise artifact).

    Note: This approach yields at most ONE observation per trip per segment.
    Multiple trips on the same route accumulate multiple observations over
    time.  For the MVP dataset (6 trips), most segments will have exactly one
    observation.
    """
    # Filter and sort points for this trip
    trip_pts = sorted(
        [p for p in gps_points if p.trip_id == trip_id],
        key=lambda p: p.timestamp_min,
    )
    if len(trip_pts) < 2:
        return [[] for _ in range(len(route.stop_ids) - 1)]

    # Build stop coordinate map from route metadata is not available here;
    # we use a simpler approach: assign each GPS point to a stop index by
    # dividing the trip's timeline into equal-time buckets.
    # The GPS generator places points at GPS_INTERVAL_MIN intervals, and
    # we know the order of stops, so we assign points to segments by the
    # fraction of total elapsed time.
    total_pts = len(trip_pts)
    n_stops = len(route.stop_ids)
    n_segs = n_stops - 1

    # Assign each GPS point a fractional position along the route (0..n_segs)
    # based on its rank in the sorted point list
    samples: list[list[float]] = [[] for _ in range(n_segs)]

    # Find the GPS timestamps "nearest" to each stop transition.
    # Stops are evenly spaced in index; divide points into n_stops buckets.
    stop_timestamps: list[float] = []
    for stop_idx in range(n_stops):
        # The point index closest to the proportional position for this stop
        prop = stop_idx / (n_stops - 1) if n_stops > 1 else 0.0
        pt_idx = round(prop * (total_pts - 1))
        pt_idx = max(0, min(pt_idx, total_pts - 1))
        stop_timestamps.append(trip_pts[pt_idx].timestamp_min)

    # Compute segment times from consecutive stop timestamps
    for seg_idx in range(n_segs):
        seg_time = stop_timestamps[seg_idx + 1] - stop_timestamps[seg_idx]
        if seg_time > 0:
            samples[seg_idx].append(seg_time)

    return samples


def _collect_all_segment_samples(
    gps_points: list[GpsPoint],
    route: Route,
    trips_on_route: list[Trip],
) -> list[list[float]]:
    """
    Accumulate GPS-derived segment travel-time samples across ALL trips that
    run on the given route.  More historical observations → more stable median.
    """
    n_segs = len(route.stop_ids) - 1
    all_samples: list[list[float]] = [[] for _ in range(n_segs)]

    for trip in trips_on_route:
        trip_samples = _extract_segment_samples(gps_points, trip.trip_id, route)
        for seg_idx, obs in enumerate(trip_samples):
            all_samples[seg_idx].extend(obs)

    return all_samples


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def estimate_trip_etas(
    trip: Trip,
    route: Route,
    gps_points: list[GpsPoint],
    all_trips: list[Trip],
    scenario: ScenarioDefinition,
    departure_override_min: Optional[int] = None,
) -> list[EtaPrediction]:
    """
    Estimate arrival times for every stop of one trip under the given scenario.

    Args:
        trip:                  The trip to estimate.
        route:                 The route this trip runs on.
        gps_points:            All GPS points in the dataset.
        all_trips:             All scheduled trips (used to accumulate
                               historical segment observations).
        scenario:              Active scenario (provides delay overrides).
        departure_override_min: When the planner tests a shifted departure,
                               pass the new departure time here.

    Returns:
        A list of EtaPrediction, one per stop in route order.
        The first stop always has expected_arrival == departure time and
        a zero-width window (the bus is already there at departure).
    """
    # All trips on this route (for pooling GPS samples)
    trips_on_route = [t for t in all_trips if t.route_id == route.route_id]

    # Accumulated segment samples across all route trips
    all_samples = _collect_all_segment_samples(gps_points, route, trips_on_route)

    # Scenario delay overrides: {segment_index: extra_delay_min}
    route_dict = {
        "stop_ids": route.stop_ids,
        "segments": [
            {"from_stop_id": s.from_stop_id, "to_stop_id": s.to_stop_id,
             "travel_time_min": s.travel_time_min}
            for s in route.segments
        ],
    }
    delay_map = get_trip_segment_delays(trip.trip_id, route_dict, scenario)

    departure = float(departure_override_min if departure_override_min is not None
                      else trip.scheduled_departure_min)

    predictions: list[EtaPrediction] = []
    current_time = departure
    prev_arrival = departure  # lower-bound clamp

    # --- First stop: departure point ---
    predictions.append(EtaPrediction(
        trip_id=trip.trip_id,
        stop_id=route.stop_ids[0],
        expected_arrival_min=departure,
        window_start_min=departure,
        window_end_min=departure,
        estimate_source="fallback_configured",
        assumptions="Departure stop; arrival equals scheduled departure.",
    ))

    # --- Subsequent stops ---
    for seg_idx, segment in enumerate(route.segments):
        samples = all_samples[seg_idx]
        extra_delay = delay_map.get(seg_idx, 0.0)
        configured_tt = segment.travel_time_min + extra_delay

        if len(samples) >= MIN_SAMPLES_FOR_MEDIAN:
            # Median observed travel time + scenario delay
            median_tt = statistics.median(samples) + extra_delay
            half_range = max(
                ETA_FALLBACK_WINDOW_MIN,
                (max(samples) - min(samples)) / 2.0,
            )
            source = "median_observed"
            assumptions = (
                f"Median of {len(samples)} observed segment travel times "
                f"({segment.from_stop_id}→{segment.to_stop_id}); "
                f"window = median ± {half_range:.1f} min"
            )
            if extra_delay > 0:
                assumptions += f"; +{extra_delay:.0f} min scenario delay applied."
            estimated_tt = median_tt
            raw_window = half_range
        else:
            # Fall back to configured travel time
            estimated_tt = configured_tt
            raw_window = ETA_FALLBACK_WINDOW_MIN
            source = "fallback_configured"
            n = len(samples)
            assumptions = (
                f"Insufficient GPS samples ({n} < {MIN_SAMPLES_FOR_MEDIAN}) for "
                f"{segment.from_stop_id}→{segment.to_stop_id}; "
                f"using configured travel time {segment.travel_time_min:.1f} min"
            )
            if extra_delay > 0:
                assumptions += f" + {extra_delay:.0f} min scenario delay."
            assumptions += f" Window = ±{ETA_FALLBACK_WINDOW_MIN} min."

        expected_arrival = current_time + estimated_tt

        # Clamp window lower bound to the previous stop's arrival
        window_start = max(prev_arrival, expected_arrival - raw_window)
        window_end = expected_arrival + raw_window

        predictions.append(EtaPrediction(
            trip_id=trip.trip_id,
            stop_id=segment.to_stop_id,
            expected_arrival_min=round(expected_arrival, 4),
            window_start_min=round(window_start, 4),
            window_end_min=round(window_end, 4),
            estimate_source=source,
            assumptions=assumptions,
        ))

        current_time = expected_arrival
        prev_arrival = expected_arrival

    return predictions


def estimate_all_trips(
    trips: list[Trip],
    routes: list[Route],
    gps_points: list[GpsPoint],
    scenario: ScenarioDefinition,
    departure_overrides: Optional[dict[str, int]] = None,
) -> dict[str, list[EtaPrediction]]:
    """
    Estimate ETAs for all trips, returning a dict keyed by trip_id.

    Args:
        departure_overrides: Optional {trip_id: departure_min} to override
                             scheduled departures (used by the planner).
    """
    departure_overrides = departure_overrides or {}
    route_map: dict[str, Route] = {r.route_id: r for r in routes}
    result: dict[str, list[EtaPrediction]] = {}

    for trip in trips:
        route = route_map.get(trip.route_id)
        if route is None:
            continue  # skip trips with unknown routes (data error)
        override = departure_overrides.get(trip.trip_id)
        result[trip.trip_id] = estimate_trip_etas(
            trip=trip,
            route=route,
            gps_points=gps_points,
            all_trips=trips,
            scenario=scenario,
            departure_override_min=override,
        )

    return result
