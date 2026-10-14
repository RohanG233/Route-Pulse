"""
Planning and recommendation engine for RoutePulse (spec §11).

Search strategy (spec §11.2):
  1. Compute baseline plan metrics and constraint violations.
  2. Generate candidates: for each trip, try:
       a. Reassign to each available bus with sufficient capacity.
       b. Shift departure by DEPARTURE_SHIFT_STEP_MIN increments within
          ±MAX_DEPARTURE_SHIFT_MIN of the scheduled departure.
     Only one change per candidate (one trip, one modification).
  3. Validate every candidate with constraint_validator (C1–C7).
     Reject candidates with any hard-constraint violation.
  4. Compute objective cost for each feasible candidate under the SAME
     scenario demand and disruptions as the baseline.
  5. Select the feasible candidate with the lowest objective cost.
     Tie-break: fewer changes first, then lexicographic by trip_id + change_key.
  6. If no candidate improves the baseline cost, report "no_improvement"
     along with the best feasible candidate (if any).
  7. If the baseline itself has hard-constraint violations, report
     "infeasible_baseline" and still attempt to find a feasible plan.

Candidate limit: bounded by number_of_trips × (number_of_buses +
2 × MAX_DEPARTURE_SHIFT_MIN / DEPARTURE_SHIFT_STEP_MIN).  For the MVP
dataset (6 trips, 4 buses, ±15 min / 5 min step = 6 shifts) this is
6 × (4 + 6) = 60 candidates — small and deterministic.

The planner never modifies route stop sequences or invents new routes (spec §11.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.config import (
    DEFAULT_OBJECTIVE_WEIGHTS,
    DEPARTURE_SHIFT_STEP_MIN,
    MAX_DEPARTURE_SHIFT_MIN,
    REPOSITIONING_BUFFER_MIN,
)
from app.schemas import (
    Bus,
    DemandRecord,
    EtaPrediction,
    GpsPoint,
    GroundTruthArrival,
    MetricSnapshot,
    PlanResponse,
    RecommendedAction,
    Route,
    ScenarioDefinition,
    Trip,
    ValidationResult,
)
from app.services.constraint_validator import validate_plan
from app.services.demand_service import simulate_all_trips
from app.services.eta_service import estimate_all_trips
from app.services.metrics_service import build_metric_snapshot, compute_metric_deltas


# ---------------------------------------------------------------------------
# Internal candidate representation
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    """One proposed change to the baseline plan."""
    trip_id: str
    bus_override: Optional[str]        # new bus_id, or None if unchanged
    departure_override: Optional[int]  # new departure_min, or None if unchanged
    change_count: int = 1              # always 1 for MVP single-trip changes
    sort_key: str = ""                 # deterministic tie-breaking string


def _build_candidates(
    trips: list[Trip],
    buses: list[Bus],
    routes: list[Route],
) -> list[_Candidate]:
    """
    Generate all single-trip change candidates.

    For each trip:
      - Try reassigning to every bus other than the current one that is available.
      - Try every departure shift in ±MAX_DEPARTURE_SHIFT_MIN steps of
        DEPARTURE_SHIFT_STEP_MIN (excluding 0).
    """
    available_buses = [b for b in buses if b.status != "unavailable"]
    candidates: list[_Candidate] = []

    for trip in trips:
        # Bus reassignment candidates
        for bus in available_buses:
            if bus.bus_id == trip.bus_id:
                continue
            candidates.append(_Candidate(
                trip_id=trip.trip_id,
                bus_override=bus.bus_id,
                departure_override=None,
                sort_key=f"{trip.trip_id}:bus:{bus.bus_id}",
            ))

        # Departure shift candidates
        shifts = range(
            -MAX_DEPARTURE_SHIFT_MIN,
            MAX_DEPARTURE_SHIFT_MIN + 1,
            DEPARTURE_SHIFT_STEP_MIN,
        )
        for shift in shifts:
            if shift == 0:
                continue
            new_dep = trip.scheduled_departure_min + shift
            candidates.append(_Candidate(
                trip_id=trip.trip_id,
                bus_override=None,
                departure_override=new_dep,
                sort_key=f"{trip.trip_id}:dep:{shift:+d}",
            ))

    # Sort deterministically: by trip_id then sort_key
    candidates.sort(key=lambda c: (c.trip_id, c.sort_key))
    return candidates


def _apply_candidate(
    candidate: _Candidate,
    base_trips: list[Trip],
) -> tuple[dict[str, str], dict[str, int]]:
    """
    Return (bus_overrides, departure_overrides) dicts for a candidate.
    """
    bus_overrides: dict[str, str] = {}
    dep_overrides: dict[str, int] = {}

    if candidate.bus_override is not None:
        bus_overrides[candidate.trip_id] = candidate.bus_override
    if candidate.departure_override is not None:
        dep_overrides[candidate.trip_id] = candidate.departure_override

    return bus_overrides, dep_overrides


def _make_action(
    candidate: _Candidate,
    base_trips: list[Trip],
    buses: list[Bus],
) -> RecommendedAction:
    """Convert a _Candidate into a RecommendedAction for the API response."""
    base_trip = next(t for t in base_trips if t.trip_id == candidate.trip_id)
    bus_map = {b.bus_id: b for b in buses}

    if candidate.bus_override is not None:
        orig_bus = bus_map.get(base_trip.bus_id)
        new_bus = bus_map.get(candidate.bus_override)
        orig_cap = orig_bus.capacity if orig_bus else "?"
        new_cap = new_bus.capacity if new_bus else "?"
        return RecommendedAction(
            action_type="reassign_bus",
            trip_id=candidate.trip_id,
            original_bus_id=base_trip.bus_id,
            new_bus_id=candidate.bus_override,
            reason=(
                f"Reassign trip {candidate.trip_id} from bus "
                f"{base_trip.bus_id} ({orig_cap} seats) to bus "
                f"{candidate.bus_override} ({new_cap} seats) to reduce "
                f"unmet demand or improve capacity utilisation."
            ),
        )
    else:
        shift = candidate.departure_override - base_trip.scheduled_departure_min
        direction = "earlier" if shift < 0 else "later"
        return RecommendedAction(
            action_type="shift_departure",
            trip_id=candidate.trip_id,
            original_departure_min=base_trip.scheduled_departure_min,
            new_departure_min=candidate.departure_override,
            reason=(
                f"Shift trip {candidate.trip_id} departure {abs(shift)} min "
                f"{direction} (from {base_trip.scheduled_departure_min} to "
                f"{candidate.departure_override} min) to better serve "
                f"passenger demand windows."
            ),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_planning(
    scenario_id: str,
    trips: list[Trip],
    buses: list[Bus],
    routes: list[Route],
    demand: list[DemandRecord],
    gps_points: list[GpsPoint],
    ground_truth: list[GroundTruthArrival],
    scenario: ScenarioDefinition,
    objective_weights: dict[str, float] | None = None,
) -> PlanResponse:
    """
    Run the full planning cycle for a scenario.

    Returns a PlanResponse containing:
      - Baseline plan metrics and validation result.
      - Best feasible proposed plan (if one exists and improves the baseline).
      - Recommendation status, actions, explanation, and metric deltas.
    """
    weights = {**DEFAULT_OBJECTIVE_WEIGHTS, **(objective_weights or {})}

    # ------------------------------------------------------------------
    # 1. Baseline plan
    # ------------------------------------------------------------------
    baseline_demand_results = simulate_all_trips(trips, routes, buses, demand)
    baseline_eta = estimate_all_trips(trips, routes, gps_points, scenario)
    baseline_validation = validate_plan(
        trips, buses, routes, demand, baseline_demand_results, scenario
    )
    baseline_metrics = build_metric_snapshot(
        trips, buses, routes, demand, baseline_demand_results, scenario,
        eta_predictions=baseline_eta,
        ground_truth=ground_truth,
        objective_weights=weights,
    )
    baseline_eta_quality = (
        _eta_quality_from_snapshot(baseline_metrics)
    )

    assumptions = [
        "Demand matched to trips by route_id + time_window_start_min == departure_min.",
        "Boarding order: lexicographic by demand_id (deterministic).",
        "Waiting time = max(0, departure_min - time_window_start_min) per demand record.",
        "GPS segment samples pooled across all trips on the same route.",
        f"ETA fallback window: ±2.0 min when < {3} GPS samples per segment.",
        "Missed connections: not modelled (no connection timetable in dataset).",
        f"Repositioning buffer: {REPOSITIONING_BUFFER_MIN} min between consecutive trips.",
    ]
    limitations = [
        "Synthetic campus data; not representative of a real fleet.",
        "Fixed route stop sequences — no dynamic re-routing.",
        "Single-trip changes only; multi-trip joint optimisation not attempted.",
        "Waiting-time formula is an approximation (passengers arrive at window start).",
        "ETA accuracy validated against simulation ground truth, not real arrivals.",
    ]

    # ------------------------------------------------------------------
    # 2. If baseline has hard violations, flag it but still search
    # ------------------------------------------------------------------
    if not baseline_validation.passed:
        rec_status_prefix = "infeasible_baseline"
    else:
        rec_status_prefix = None

    # ------------------------------------------------------------------
    # 3. Generate and evaluate candidates
    # ------------------------------------------------------------------
    candidates = _build_candidates(trips, buses, routes)

    best_candidate: Optional[_Candidate] = None
    best_metrics: Optional[MetricSnapshot] = None
    best_validation: Optional[ValidationResult] = None
    best_cost = baseline_metrics.objective_cost

    for cand in candidates:
        bus_ov, dep_ov = _apply_candidate(cand, trips)

        # Validate candidate
        cand_demand_results = simulate_all_trips(
            trips, routes, buses, demand,
            departure_overrides=dep_ov,
            bus_overrides=bus_ov,
        )
        cand_validation = validate_plan(
            trips, buses, routes, demand, cand_demand_results, scenario,
            departure_overrides=dep_ov,
            bus_overrides=bus_ov,
        )
        if not cand_validation.passed:
            continue  # reject infeasible candidates

        # Compute metrics under the SAME scenario inputs
        cand_eta = estimate_all_trips(
            trips, routes, gps_points, scenario,
            departure_overrides=dep_ov,
        )
        cand_metrics = build_metric_snapshot(
            trips, buses, routes, demand, cand_demand_results, scenario,
            eta_predictions=cand_eta,
            ground_truth=ground_truth,
            departure_overrides=dep_ov,
            bus_overrides=bus_ov,
            objective_weights=weights,
        )

        cost = cand_metrics.objective_cost
        if cost < best_cost:
            best_cost = cost
            best_candidate = cand
            best_metrics = cand_metrics
            best_validation = cand_validation
        elif cost == best_cost and best_candidate is not None:
            # Tie-break: fewer changes first (both are 1), then lex sort_key
            if cand.sort_key < best_candidate.sort_key:
                best_candidate = cand
                best_metrics = cand_metrics
                best_validation = cand_validation

    # ------------------------------------------------------------------
    # 4. Build response
    # ------------------------------------------------------------------
    if best_candidate is not None and best_metrics is not None:
        if rec_status_prefix == "infeasible_baseline":
            rec_status = "infeasible_baseline"
            summary = (
                f"The baseline plan has hard-constraint violations. "
                f"A feasible alternative was found: "
                f"{_candidate_summary(best_candidate, trips)}."
            )
        else:
            rec_status = "improvement_found"
            improvement = round(
                (baseline_metrics.objective_cost - best_metrics.objective_cost)
                / baseline_metrics.objective_cost * 100, 1
            ) if baseline_metrics.objective_cost > 0 else 0.0
            summary = (
                f"Feasible improvement found: "
                f"{_candidate_summary(best_candidate, trips)}. "
                f"Objective cost reduced by {improvement}% "
                f"({baseline_metrics.objective_cost:.1f} → "
                f"{best_metrics.objective_cost:.1f})."
            )

        action = _make_action(best_candidate, trips, buses)
        bus_ov, dep_ov = _apply_candidate(best_candidate, trips)
        deltas = compute_metric_deltas(baseline_metrics, best_metrics)

        return PlanResponse(
            scenario_id=scenario_id,
            recommendation_status=rec_status,
            recommendation_summary=summary,
            actions=[action],
            constraint_validation=best_validation,
            baseline_metrics=baseline_metrics,
            proposed_metrics=best_metrics,
            metric_deltas=deltas,
            eta_quality=baseline_eta_quality,
            assumptions=assumptions,
            limitations=limitations,
        )

    else:
        # No improvement found
        if rec_status_prefix == "infeasible_baseline":
            rec_status = "infeasible_baseline"
            summary = (
                "The baseline plan has hard-constraint violations and no "
                "feasible improving candidate was found. "
                "Manual intervention required."
            )
        else:
            rec_status = "no_improvement"
            summary = (
                "No feasible candidate improves the baseline objective cost. "
                "The baseline plan is already optimal within the search space "
                "(single-trip changes, configured bounds)."
            )

        return PlanResponse(
            scenario_id=scenario_id,
            recommendation_status=rec_status,
            recommendation_summary=summary,
            actions=[],
            constraint_validation=baseline_validation,
            baseline_metrics=baseline_metrics,
            proposed_metrics=None,
            metric_deltas=[],
            eta_quality=baseline_eta_quality,
            assumptions=assumptions,
            limitations=limitations,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candidate_summary(candidate: _Candidate, trips: list[Trip]) -> str:
    base_trip = next(t for t in trips if t.trip_id == candidate.trip_id)
    if candidate.bus_override:
        return (
            f"reassign trip {candidate.trip_id} to bus {candidate.bus_override}"
        )
    else:
        shift = candidate.departure_override - base_trip.scheduled_departure_min
        direction = "earlier" if shift < 0 else "later"
        return (
            f"shift trip {candidate.trip_id} departure {abs(shift)} min {direction}"
        )


def _eta_quality_from_snapshot(snapshot: MetricSnapshot):
    """Extract EtaQualityMetrics from a snapshot if present."""
    from app.schemas import EtaQualityMetrics
    if snapshot.eta_mae_min is not None and snapshot.eta_window_coverage_pct is not None:
        return EtaQualityMetrics(
            mae_minutes=snapshot.eta_mae_min,
            evaluated_count=0,   # count not stored in snapshot; use 0 as placeholder
            window_coverage_pct=snapshot.eta_window_coverage_pct,
        )
    return None
