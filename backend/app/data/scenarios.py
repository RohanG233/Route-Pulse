"""
RoutePulse scenario definitions and transformation logic.

Each scenario is a named, deterministic transformation of the baseline dataset.
The SAME transformed demand and disruptions are applied to BOTH the baseline
plan evaluation and the proposed plan evaluation — no asymmetric inputs.

Scenario IDs:
    normal              Base demand, no disruptions, all buses available.
    demand_spike        R2 demand multiplied by DEMAND_SPIKE_MULTIPLIER.
    bus_delay           Trip T3 delayed by BUS_DELAY_MINUTES on every segment.
    vehicle_unavailable Bus B1 marked unavailable for the entire service window.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from app.config import (
    BUS_DELAY_MINUTES,
    DEMAND_SPIKE_MULTIPLIER,
    VEHICLE_UNAVAILABLE_BUS,
)
from app.schemas import ScenarioDefinition


# ---------------------------------------------------------------------------
# Canonical scenario registry
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, ScenarioDefinition] = {
    "normal": ScenarioDefinition(
        scenario_id="normal",
        name="Normal",
        description=(
            "Baseline demand and travel times; all buses available. "
            "Computes ETAs and metrics with no intentional hard-constraint "
            "violations. Useful as a reference for comparison."
        ),
    ),
    "demand_spike": ScenarioDefinition(
        scenario_id="demand_spike",
        name="Demand Spike",
        description=(
            f"Route R2 demand multiplied by {DEMAND_SPIKE_MULTIPLIER}× "
            "(rounds up fractional passengers). "
            "This overloads bus B1 on trip T3 and leaves unmet demand. "
            "The planner should detect excess demand and recommend a feasible "
            "reallocation if one exists, otherwise report infeasibility."
        ),
        demand_overrides={"R2": DEMAND_SPIKE_MULTIPLIER},
    ),
    "bus_delay": ScenarioDefinition(
        scenario_id="bus_delay",
        name="Bus Delay",
        description=(
            f"Trip T3 (Route R2, 08:00) experiences a uniform {BUS_DELAY_MINUTES}-minute "
            "delay added to every segment. ETA windows are updated, late arrivals "
            "are flagged, and the planner re-evaluates the plan under the delay."
        ),
        segment_delay_overrides={"T3": float(BUS_DELAY_MINUTES)},
    ),
    "vehicle_unavailable": ScenarioDefinition(
        scenario_id="vehicle_unavailable",
        name="Vehicle Unavailable",
        description=(
            f"Bus {VEHICLE_UNAVAILABLE_BUS} is marked unavailable for the entire "
            "service window. It must not be assigned to any trip. The planner "
            "must find a feasible alternative or clearly report infeasibility."
        ),
        unavailable_bus_ids=[VEHICLE_UNAVAILABLE_BUS],
    ),
}


# ---------------------------------------------------------------------------
# Dataset transformation helpers
# ---------------------------------------------------------------------------

def apply_demand_transforms(
    demand: list[dict],
    scenario: ScenarioDefinition,
) -> list[dict]:
    """
    Return a new demand list with scenario multipliers applied.

    Fractional passengers are rounded UP (math.ceil) to avoid losing
    small groups in multiplication.  The global demand_multiplier is applied
    first, then route-specific overrides compound on top of it.

    The original list is never mutated.
    """
    result = []
    for row in demand:
        new_row = dict(row)
        multiplier = scenario.demand_multiplier

        # Apply route-specific override if present
        route_mult = scenario.demand_overrides.get(row["route_id"])
        if route_mult is not None:
            multiplier *= route_mult

        if multiplier != 1.0:
            new_row["passengers"] = math.ceil(row["passengers"] * multiplier)

        result.append(new_row)
    return result


def apply_bus_availability(
    buses: list[dict],
    scenario: ScenarioDefinition,
) -> list[dict]:
    """
    Return a new buses list with unavailable buses marked.

    The original list is never mutated.
    """
    result = []
    for bus in buses:
        new_bus = dict(bus)
        if bus["bus_id"] in scenario.unavailable_bus_ids:
            new_bus["status"] = "unavailable"
        result.append(new_bus)
    return result


def get_trip_segment_delays(
    trip_id: str,
    route: dict,
    scenario: ScenarioDefinition,
) -> dict[int, float]:
    """
    Return a mapping of {segment_index: extra_delay_minutes} for a trip.

    If the scenario specifies a delay for this trip_id, it applies uniformly
    to every segment (the spec says 'selected route segment'; we use all
    segments for simplicity and document the assumption).
    """
    extra = scenario.segment_delay_overrides.get(trip_id, 0.0)
    if extra == 0.0:
        return {}
    n_segments = len(route["stop_ids"]) - 1
    return {i: extra for i in range(n_segments)}


def _to_dict(obj) -> dict:
    """
    Normalise a value to a plain dict.

    Accepts either a dict (passed through as-is) or a Pydantic BaseModel
    (converted via model_dump()).  This lets build_scenario_dataset work
    with both raw dicts from generate_all() and typed Pydantic objects
    from data_loader.load_all().
    """
    if isinstance(obj, dict):
        return obj
    # Pydantic v2 BaseModel
    return obj.model_dump()


def build_scenario_dataset(
    base_stops,
    base_buses,
    base_routes,
    base_schedules,
    base_demand,
    base_gps_traces,
    base_ground_truth,
    scenario: ScenarioDefinition,
) -> dict[str, Any]:
    """
    Apply all scenario transformations to the base dataset.

    Accepts either plain dicts (from generate_all) or typed Pydantic objects
    (from load_all); normalises everything to dicts, applies transforms, then
    converts back to typed Pydantic objects so all services work uniformly.

    Returned dict keys (both 'trips' and 'schedules' for backward compat):
        stops, buses, routes, trips, schedules, demand, gps_traces, ground_truth
    """
    from app.schemas import (
        Bus, DemandRecord, GpsPoint, GroundTruthArrival, Route,
        RouteSegment, Stop, Trip,
    )

    # Normalise all inputs to plain dicts
    stops_d = [_to_dict(s) for s in base_stops]
    buses_d = [_to_dict(b) for b in base_buses]
    routes_d = [_to_dict(r) for r in base_routes]
    trips_d = [_to_dict(t) for t in base_schedules]
    demand_d = [_to_dict(d) for d in base_demand]
    gps_d = [_to_dict(g) for g in base_gps_traces]
    gt_d = [_to_dict(g) for g in base_ground_truth]

    # Apply scenario transformations (dict-based)
    sc_buses_d = apply_bus_availability(buses_d, scenario)
    sc_demand_d = apply_demand_transforms(demand_d, scenario)

    # Convert back to typed Pydantic objects
    typed_stops = [Stop(**s) for s in stops_d]
    typed_buses = [Bus(**b) for b in sc_buses_d]
    typed_routes = [
        Route(
            route_id=r["route_id"],
            name=r["name"],
            stop_ids=r["stop_ids"],
            segments=[RouteSegment(**seg) for seg in r["segments"]],
        )
        for r in routes_d
    ]
    typed_trips = [Trip(**t) for t in trips_d]
    typed_demand = [DemandRecord(**d) for d in sc_demand_d]
    typed_gps = [GpsPoint(**g) for g in gps_d]
    typed_gt = [GroundTruthArrival(**g) for g in gt_d]

    return {
        "stops":        typed_stops,
        "buses":        typed_buses,
        "routes":       typed_routes,
        "trips":        typed_trips,    # services use this key
        "schedules":    typed_trips,    # backward compat for raw-dict callers
        "demand":       typed_demand,
        "gps_traces":   typed_gps,
        "ground_truth": typed_gt,
        "scenario":     scenario,
    }


def get_scenario(scenario_id: str) -> ScenarioDefinition:
    """Look up a scenario by ID; raises KeyError with a helpful message."""
    if scenario_id not in SCENARIOS:
        valid = ", ".join(SCENARIOS.keys())
        raise KeyError(
            f"Unknown scenario_id '{scenario_id}'. Valid options: {valid}"
        )
    return SCENARIOS[scenario_id]


def list_scenarios() -> list[dict]:
    """Return a list of scenario summary dicts for the API."""
    return [
        {
            "scenario_id": s.scenario_id,
            "name": s.name,
            "description": s.description,
        }
        for s in SCENARIOS.values()
    ]
