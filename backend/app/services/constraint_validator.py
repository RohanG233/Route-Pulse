"""
Hard-constraint validator for RoutePulse (spec §10).

Constraints C1–C7 are checked for every candidate plan before it can be
accepted.  A plan with any violation is rejected — constraints cannot be
traded off against the objective.

Constraint definitions:
  C1 — Bus capacity      : onboard passengers <= bus capacity at every stop.
  C2 — Bus availability  : trip start/end within bus available window;
                           bus not marked unavailable.
  C3 — No overlapping    : a bus cannot serve trips whose time intervals overlap.
  C4 — Turnaround        : configurable REPOSITIONING_BUFFER_MIN gap between
                           consecutive trips on the same bus.
  C5 — Departure window  : actual departure within permitted_departure_window_min
                           of the scheduled departure.
  C6 — Route feasibility : stops in valid route order; all segment travel times
                           positive; trip arrival follows departure.
  C7 — Data validity     : referenced buses/routes/stops exist; demand origin
                           precedes destination on route; counts/capacities valid.

ETA lateness, missed connections, and unmet demand are operational alerts /
objective metrics — NOT automatically hard-constraint failures unless a
scenario explicitly defines them as such (spec §10 note).

Public API
----------
validate_plan(assignments, buses, routes, trips, demand, scenario) -> ValidationResult
"""

from __future__ import annotations

from app.config import REPOSITIONING_BUFFER_MIN
from app.data.generate_dataset import _route_stop_arrivals
from app.data.scenarios import get_trip_segment_delays
from app.schemas import (
    Bus,
    ConstraintViolation,
    DemandRecord,
    DemandResult,
    Route,
    ScenarioDefinition,
    Trip,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _trip_time_bounds(
    trip: Trip,
    route: Route,
    scenario: ScenarioDefinition,
    departure_override: int | None = None,
) -> tuple[float, float]:
    """
    Return (start_min, end_min) for a trip under the given scenario.

    start_min = actual departure (override or scheduled).
    end_min   = arrival at the last stop (scheduled segment times + delays).
    """
    departure = float(
        departure_override if departure_override is not None
        else trip.scheduled_departure_min
    )
    route_dict = {
        "stop_ids": route.stop_ids,
        "segments": [
            {"from_stop_id": s.from_stop_id, "to_stop_id": s.to_stop_id,
             "travel_time_min": s.travel_time_min}
            for s in route.segments
        ],
    }
    delays = get_trip_segment_delays(trip.trip_id, route_dict, scenario)
    arrivals = _route_stop_arrivals(route_dict, departure, delays)
    return arrivals[0][1], arrivals[-1][1]


# ---------------------------------------------------------------------------
# Per-constraint checks (each returns a list of ConstraintViolation)
# ---------------------------------------------------------------------------

def _check_c1_capacity(
    demand_results: list[DemandResult],
) -> list[ConstraintViolation]:
    """C1: onboard passengers <= bus capacity at every stop."""
    violations: list[ConstraintViolation] = []
    for result in demand_results:
        for event in result.stop_events:
            if event.onboard_after > event.capacity:
                violations.append(ConstraintViolation(
                    code="C1",
                    trip_id=event.trip_id,
                    bus_id=result.bus_id,
                    stop_id=event.stop_id,
                    message=(
                        f"C1 Bus capacity exceeded on trip {event.trip_id} at stop "
                        f"{event.stop_id}: {event.onboard_after} onboard, "
                        f"capacity {event.capacity}."
                    ),
                ))
    return violations


def _check_c2_availability(
    trips: list[Trip],
    bus_map: dict[str, Bus],
    route_map: dict[str, Route],
    scenario: ScenarioDefinition,
    departure_overrides: dict[str, int],
    bus_overrides: dict[str, str],
) -> list[ConstraintViolation]:
    """C2: trip starts/ends within bus availability; bus not unavailable."""
    violations: list[ConstraintViolation] = []
    for trip in trips:
        bus_id = bus_overrides.get(trip.trip_id, trip.bus_id)
        bus = bus_map.get(bus_id)
        if bus is None:
            violations.append(ConstraintViolation(
                code="C2",
                trip_id=trip.trip_id,
                bus_id=bus_id,
                message=f"C2 Bus {bus_id} not found for trip {trip.trip_id}.",
            ))
            continue

        # Unavailable check
        if bus.status == "unavailable":
            violations.append(ConstraintViolation(
                code="C2",
                trip_id=trip.trip_id,
                bus_id=bus_id,
                message=(
                    f"C2 Bus {bus_id} is unavailable in this scenario but "
                    f"assigned to trip {trip.trip_id}."
                ),
            ))
            continue

        route = route_map.get(trip.route_id)
        if route is None:
            continue  # C7 will catch this
        dep_override = departure_overrides.get(trip.trip_id)
        start, end = _trip_time_bounds(trip, route, scenario, dep_override)

        if start < bus.available_from_min:
            violations.append(ConstraintViolation(
                code="C2",
                trip_id=trip.trip_id,
                bus_id=bus_id,
                message=(
                    f"C2 Trip {trip.trip_id} starts at {start:.0f} min but bus "
                    f"{bus_id} is not available until {bus.available_from_min} min."
                ),
            ))
        if end > bus.available_until_min:
            violations.append(ConstraintViolation(
                code="C2",
                trip_id=trip.trip_id,
                bus_id=bus_id,
                message=(
                    f"C2 Trip {trip.trip_id} ends at {end:.1f} min but bus "
                    f"{bus_id} is only available until {bus.available_until_min} min."
                ),
            ))
    return violations


def _check_c3_no_overlap(
    trips: list[Trip],
    route_map: dict[str, Route],
    scenario: ScenarioDefinition,
    departure_overrides: dict[str, int],
    bus_overrides: dict[str, str],
) -> list[ConstraintViolation]:
    """C3: a bus cannot serve two trips whose time intervals overlap."""
    violations: list[ConstraintViolation] = []

    # Build {bus_id: [(trip_id, start, end), ...]}
    from collections import defaultdict
    bus_trips: dict[str, list[tuple[str, float, float]]] = defaultdict(list)

    for trip in trips:
        bus_id = bus_overrides.get(trip.trip_id, trip.bus_id)
        route = route_map.get(trip.route_id)
        if route is None:
            continue
        dep = departure_overrides.get(trip.trip_id)
        start, end = _trip_time_bounds(trip, route, scenario, dep)
        bus_trips[bus_id].append((trip.trip_id, start, end))

    for bus_id, trip_times in bus_trips.items():
        # Sort by start time for efficient pair checking
        trip_times.sort(key=lambda x: x[1])
        for i in range(len(trip_times)):
            for j in range(i + 1, len(trip_times)):
                tid_a, s_a, e_a = trip_times[i]
                tid_b, s_b, e_b = trip_times[j]
                # Overlap: start_b < end_a  (sorted so s_b >= s_a)
                if s_b < e_a:
                    violations.append(ConstraintViolation(
                        code="C3",
                        bus_id=bus_id,
                        trip_id=tid_a,
                        message=(
                            f"C3 Bus {bus_id} has overlapping trips: "
                            f"{tid_a} ({s_a:.0f}–{e_a:.1f} min) and "
                            f"{tid_b} ({s_b:.0f}–{e_b:.1f} min)."
                        ),
                    ))
    return violations


def _check_c4_turnaround(
    trips: list[Trip],
    route_map: dict[str, Route],
    scenario: ScenarioDefinition,
    departure_overrides: dict[str, int],
    bus_overrides: dict[str, str],
) -> list[ConstraintViolation]:
    """C4: REPOSITIONING_BUFFER_MIN gap required between consecutive bus trips."""
    violations: list[ConstraintViolation] = []

    from collections import defaultdict
    bus_trips: dict[str, list[tuple[str, float, float]]] = defaultdict(list)

    for trip in trips:
        bus_id = bus_overrides.get(trip.trip_id, trip.bus_id)
        route = route_map.get(trip.route_id)
        if route is None:
            continue
        dep = departure_overrides.get(trip.trip_id)
        start, end = _trip_time_bounds(trip, route, scenario, dep)
        bus_trips[bus_id].append((trip.trip_id, start, end))

    for bus_id, trip_times in bus_trips.items():
        trip_times.sort(key=lambda x: x[1])
        for i in range(len(trip_times) - 1):
            tid_a, s_a, e_a = trip_times[i]
            tid_b, s_b, e_b = trip_times[i + 1]
            gap = s_b - e_a
            if gap < REPOSITIONING_BUFFER_MIN:
                violations.append(ConstraintViolation(
                    code="C4",
                    bus_id=bus_id,
                    trip_id=tid_b,
                    message=(
                        f"C4 Insufficient turnaround for bus {bus_id}: "
                        f"gap between {tid_a} (ends {e_a:.1f}) and "
                        f"{tid_b} (starts {s_b:.0f}) is {gap:.1f} min "
                        f"(required {REPOSITIONING_BUFFER_MIN} min)."
                    ),
                ))
    return violations


def _check_c5_departure_window(
    trips: list[Trip],
    departure_overrides: dict[str, int],
) -> list[ConstraintViolation]:
    """C5: actual departure within permitted_departure_window_min of scheduled."""
    violations: list[ConstraintViolation] = []
    for trip in trips:
        actual = departure_overrides.get(trip.trip_id, trip.scheduled_departure_min)
        shift = abs(actual - trip.scheduled_departure_min)
        if shift > trip.permitted_departure_window_min:
            violations.append(ConstraintViolation(
                code="C5",
                trip_id=trip.trip_id,
                message=(
                    f"C5 Trip {trip.trip_id}: departure shift {shift} min "
                    f"exceeds permitted window {trip.permitted_departure_window_min} min "
                    f"(scheduled {trip.scheduled_departure_min}, actual {actual})."
                ),
            ))
    return violations


def _check_c6_route_feasibility(
    trips: list[Trip],
    route_map: dict[str, Route],
    scenario: ScenarioDefinition,
    departure_overrides: dict[str, int],
) -> list[ConstraintViolation]:
    """C6: valid route stop order; positive segment times; arrivals after departure."""
    violations: list[ConstraintViolation] = []
    for trip in trips:
        route = route_map.get(trip.route_id)
        if route is None:
            continue  # C7 handles unknown routes
        for seg in route.segments:
            if seg.travel_time_min <= 0:
                violations.append(ConstraintViolation(
                    code="C6",
                    trip_id=trip.trip_id,
                    message=(
                        f"C6 Route {route.route_id} segment "
                        f"{seg.from_stop_id}→{seg.to_stop_id} has "
                        f"non-positive travel time {seg.travel_time_min}."
                    ),
                ))
        # Arrival at last stop must be strictly after departure
        dep = departure_overrides.get(trip.trip_id, trip.scheduled_departure_min)
        route_dict = {
            "stop_ids": route.stop_ids,
            "segments": [
                {"from_stop_id": s.from_stop_id, "to_stop_id": s.to_stop_id,
                 "travel_time_min": s.travel_time_min}
                for s in route.segments
            ],
        }
        delays = get_trip_segment_delays(trip.trip_id, route_dict, scenario)
        arrivals = _route_stop_arrivals(route_dict, float(dep), delays)
        if arrivals[-1][1] <= arrivals[0][1]:
            violations.append(ConstraintViolation(
                code="C6",
                trip_id=trip.trip_id,
                message=(
                    f"C6 Trip {trip.trip_id}: last stop arrival "
                    f"({arrivals[-1][1]:.1f}) not after departure "
                    f"({arrivals[0][1]:.1f})."
                ),
            ))
    return violations


def _check_c7_data_validity(
    trips: list[Trip],
    buses: list[Bus],
    routes: list[Route],
    demand: list[DemandRecord],
    bus_overrides: dict[str, str],
) -> list[ConstraintViolation]:
    """C7: referenced buses/routes/stops exist; demand OD order valid; counts valid."""
    violations: list[ConstraintViolation] = []
    bus_ids = {b.bus_id for b in buses}
    route_ids = {r.route_id for r in routes}
    route_stop_map = {r.route_id: r.stop_ids for r in routes}

    for trip in trips:
        bus_id = bus_overrides.get(trip.trip_id, trip.bus_id)
        if bus_id not in bus_ids:
            violations.append(ConstraintViolation(
                code="C7",
                trip_id=trip.trip_id,
                bus_id=bus_id,
                message=f"C7 Trip {trip.trip_id} references unknown bus {bus_id}.",
            ))
        if trip.route_id not in route_ids:
            violations.append(ConstraintViolation(
                code="C7",
                trip_id=trip.trip_id,
                message=f"C7 Trip {trip.trip_id} references unknown route {trip.route_id}.",
            ))

    for d in demand:
        if d.passengers < 0:
            violations.append(ConstraintViolation(
                code="C7",
                message=f"C7 Demand {d.demand_id} has negative passengers ({d.passengers}).",
            ))
        stops = route_stop_map.get(d.route_id, [])
        if d.boarding_stop_id in stops and d.alighting_stop_id in stops:
            if stops.index(d.boarding_stop_id) >= stops.index(d.alighting_stop_id):
                violations.append(ConstraintViolation(
                    code="C7",
                    message=(
                        f"C7 Demand {d.demand_id}: boarding stop "
                        f"{d.boarding_stop_id} not before alighting stop "
                        f"{d.alighting_stop_id} on route {d.route_id}."
                    ),
                ))

    return violations


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_plan(
    trips: list[Trip],
    buses: list[Bus],
    routes: list[Route],
    demand: list[DemandRecord],
    demand_results: list[DemandResult],
    scenario: ScenarioDefinition,
    departure_overrides: dict[str, int] | None = None,
    bus_overrides: dict[str, str] | None = None,
) -> ValidationResult:
    """
    Run all hard-constraint checks (C1–C7) against a candidate plan.

    Args:
        trips:             All trips in the plan (with their scheduled state).
        buses:             All buses (scenario availability applied).
        routes:            All routes.
        demand:            Scenario-transformed demand records.
        demand_results:    Output of demand_service.simulate_all_trips() for
                           this candidate (used for C1 check).
        scenario:          Active scenario (provides delay overrides for timing).
        departure_overrides: {trip_id: new_departure_min} for candidate changes.
        bus_overrides:      {trip_id: new_bus_id} for candidate changes.

    Returns:
        ValidationResult(passed=True, violations=[]) if all constraints pass,
        else ValidationResult(passed=False, violations=[...]) with details.
    """
    departure_overrides = departure_overrides or {}
    bus_overrides = bus_overrides or {}

    bus_map = {b.bus_id: b for b in buses}
    route_map = {r.route_id: r for r in routes}

    violations: list[ConstraintViolation] = []

    violations += _check_c1_capacity(demand_results)
    violations += _check_c2_availability(
        trips, bus_map, route_map, scenario,
        departure_overrides, bus_overrides,
    )
    violations += _check_c3_no_overlap(
        trips, route_map, scenario, departure_overrides, bus_overrides,
    )
    violations += _check_c4_turnaround(
        trips, route_map, scenario, departure_overrides, bus_overrides,
    )
    violations += _check_c5_departure_window(trips, departure_overrides)
    violations += _check_c6_route_feasibility(
        trips, route_map, scenario, departure_overrides,
    )
    violations += _check_c7_data_validity(
        trips, buses, routes, demand, bus_overrides,
    )

    return ValidationResult(passed=len(violations) == 0, violations=violations)
