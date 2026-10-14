"""
Demand and passenger load simulation service for RoutePulse.

Algorithm (spec §9):
  For each trip, walk the route stop-by-stop in order:
    1. Alight all passengers whose destination is the current stop.
    2. Board waiting passengers whose origin is the current stop and whose
       time_window_start_min equals the trip's departure_min.
       Board in demand_id lexicographic order until the bus is full.
       Any remaining passengers are counted as unmet demand at this stop.
    3. Record a StopLoadEvent: boarded, alighted, onboard_after, capacity,
       overloaded flag, and unmet_at_stop count.

Allocation rule (deterministic, documented in assumptions.md §3.3):
  Board passengers in demand_id lexicographic order.  This is explicit and
  reproducible regardless of dict/list ordering.

Demand matching (documented in assumptions.md §3.1):
  A demand record is served by a trip when:
    - demand.route_id == trip.route_id
    - demand.time_window_start_min == trip.departure_min

  Using == (not <=) ensures each demand record maps to exactly one trip,
  avoiding double-counting across trips on the same route.

The same formula is used for baseline and proposed plans; the demand list
passed in already contains the scenario multiplier applied.
"""

from __future__ import annotations

from collections import defaultdict

from app.schemas import (
    Bus,
    DemandRecord,
    DemandResult,
    Route,
    StopLoadEvent,
    Trip,
)


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

# Tracks passengers already committed to a specific trip stop sequence.
# key = (trip_id, alighting_stop_id), value = passenger count
_OnboardGroup = tuple[str, int]   # (demand_id, passenger_count)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simulate_trip_demand(
    trip: Trip,
    route: Route,
    bus: Bus,
    demand_records: list[DemandRecord],
    actual_departure_min: int | None = None,
) -> DemandResult:
    """
    Simulate passenger boarding and alighting for one trip.

    Args:
        trip:                The trip being simulated.
        route:               The route this trip runs on.
        bus:                 The bus assigned to this trip (provides capacity).
        demand_records:      All demand records (all routes/windows); the
                             function filters to those matching this trip.
        actual_departure_min: Departure time to use for demand matching.
                             Defaults to trip.scheduled_departure_min.

    Returns:
        DemandResult with per-stop events and aggregate totals.
    """
    departure = actual_departure_min if actual_departure_min is not None \
        else trip.scheduled_departure_min
    capacity = bus.capacity

    # --- Filter demand for this trip ---
    # Matching criteria: same route, time_window_start == departure
    matching: list[DemandRecord] = [
        d for d in demand_records
        if d.route_id == trip.route_id
        and d.time_window_start_min == departure
    ]
    # Sort by demand_id for deterministic boarding order
    matching.sort(key=lambda d: d.demand_id)

    # Group matching demand by boarding stop
    # boarding_stop_id -> list of (demand_id, alighting_stop_id, passengers)
    board_at: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for d in matching:
        board_at[d.boarding_stop_id].append(
            (d.demand_id, d.alighting_stop_id, d.passengers)
        )

    # Track onboard groups: list of (alighting_stop_id, passengers)
    # We don't need demand_id after boarding; just the alighting stop and count.
    onboard: list[tuple[str, int]] = []  # (alighting_stop_id, passengers)
    total_served = 0
    total_unmet = 0
    stop_events: list[StopLoadEvent] = []

    for stop_id in route.stop_ids:
        # 1. Alight passengers whose destination is this stop
        alighted = 0
        still_onboard: list[tuple[str, int]] = []
        for (alight_stop, count) in onboard:
            if alight_stop == stop_id:
                alighted += count
            else:
                still_onboard.append((alight_stop, count))
        onboard = still_onboard

        # 2. Board waiting passengers at this stop.
        # seats_left is recomputed from onboard after each group boards,
        # so already-boarded groups within this stop are accounted for.
        boarded = 0
        unmet_here = 0
        for (demand_id, alight_stop, pax) in board_at.get(stop_id, []):
            seats_left = capacity - sum(c for _, c in onboard)
            if pax <= seats_left:
                # Board the entire demand group
                boarded += pax
                onboard.append((alight_stop, pax))
                total_served += pax
            else:
                # Partial boarding: fill available seats, rest are unmet
                can_board = seats_left
                left_behind = pax - can_board
                if can_board > 0:
                    boarded += can_board
                    onboard.append((alight_stop, can_board))
                    total_served += can_board
                unmet_here += left_behind
                total_unmet += left_behind

        onboard_after = sum(c for _, c in onboard)
        overloaded = onboard_after > capacity

        stop_events.append(StopLoadEvent(
            trip_id=trip.trip_id,
            stop_id=stop_id,
            boarded=boarded,
            alighted=alighted,
            onboard_after=onboard_after,
            capacity=capacity,
            overloaded=overloaded,
            unmet_at_stop=unmet_here,
        ))

    max_load = max((e.onboard_after for e in stop_events), default=0)
    utilization = max_load / capacity if capacity > 0 else 0.0

    return DemandResult(
        trip_id=trip.trip_id,
        bus_id=bus.bus_id,
        capacity=capacity,
        stop_events=stop_events,
        total_served=total_served,
        total_unmet=total_unmet,
        max_load=max_load,
        capacity_utilization=round(utilization, 4),
    )


def simulate_all_trips(
    trips: list[Trip],
    routes: list[Route],
    buses: list[Bus],
    demand_records: list[DemandRecord],
    departure_overrides: dict[str, int] | None = None,
    bus_overrides: dict[str, str] | None = None,
) -> list[DemandResult]:
    """
    Simulate passenger demand across all trips.

    Args:
        trips:              All scheduled trips.
        routes:             All routes.
        buses:              All buses (including availability status).
        demand_records:     Scenario-transformed demand (already multiplied).
        departure_overrides: {trip_id: new_departure_min} for planner candidates.
        bus_overrides:       {trip_id: new_bus_id} for planner candidates.

    Returns:
        List of DemandResult, one per trip.
    """
    departure_overrides = departure_overrides or {}
    bus_overrides = bus_overrides or {}

    route_map: dict[str, Route] = {r.route_id: r for r in routes}
    bus_map: dict[str, Bus] = {b.bus_id: b for b in buses}

    results: list[DemandResult] = []
    for trip in trips:
        route = route_map.get(trip.route_id)
        bus_id = bus_overrides.get(trip.trip_id, trip.bus_id)
        bus = bus_map.get(bus_id)
        if route is None or bus is None:
            continue  # skip invalid trips
        departure = departure_overrides.get(trip.trip_id)
        results.append(
            simulate_trip_demand(trip, route, bus, demand_records, departure)
        )

    return results


# ---------------------------------------------------------------------------
# Aggregate helpers used by metrics_service and alert_service
# ---------------------------------------------------------------------------

def total_unmet(results: list[DemandResult]) -> int:
    """Sum of unmet passengers across all trips."""
    return sum(r.total_unmet for r in results)


def total_served(results: list[DemandResult]) -> int:
    """Sum of served passengers across all trips."""
    return sum(r.total_served for r in results)


def capacity_violation_count(results: list[DemandResult]) -> int:
    """Count of stop-level events where onboard load exceeded bus capacity."""
    return sum(
        1 for r in results
        for e in r.stop_events
        if e.overloaded
    )


def overloaded_trips(results: list[DemandResult]) -> list[str]:
    """Return trip_ids of trips that have at least one overloaded stop event."""
    return [
        r.trip_id
        for r in results
        if any(e.overloaded for e in r.stop_events)
    ]
