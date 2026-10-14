"""
Metrics computation service for RoutePulse (spec §12).

Computes all plan-level metrics from demand simulation results, ETA
predictions, and ground-truth arrivals.  All formulas are documented here
and in docs/assumptions.md.

Metric definitions:
  avg_waiting_time_min  : total_waiting_minutes / served_passengers.
                          Unserved passengers are excluded from the denominator
                          (stated explicitly in output).
  unmet_passengers      : passengers not boarded due to capacity / service.
  capacity_violations   : stop events where onboard > capacity (demand_service
                          caps boarding at capacity, so this is normally 0;
                          the constraint_validator is the authoritative check).
  fleet_utilization_pct : sum(trip_service_min) / sum(bus_available_min) * 100.
  eta_mae_min           : MAE(predicted, ground_truth) over evaluated pairs.
  eta_window_coverage_pct: % of ground-truth arrivals inside predicted window.
  missed_connections    : "not_modelled" — no connection timetable in MVP.
  objective_cost        : weighted sum per DEFAULT_OBJECTIVE_WEIGHTS.

Waiting-time formula (documented in assumptions.md §6.1):
  wait = max(0, trip_departure_min - demand.time_window_start_min)
  Applied identically to baseline and proposed plans.

Zero-baseline handling (spec §12):
  If baseline metric is zero, pct_change is None (displayed as N/A).
"""

from __future__ import annotations

from app.config import DEFAULT_OBJECTIVE_WEIGHTS
from app.schemas import (
    Bus,
    DemandRecord,
    DemandResult,
    EtaPrediction,
    EtaQualityMetrics,
    GroundTruthArrival,
    MetricDelta,
    MetricSnapshot,
    Trip,
)
from app.services.demand_service import capacity_violation_count, total_unmet


# ---------------------------------------------------------------------------
# Waiting-time calculation
# ---------------------------------------------------------------------------

def compute_total_waiting_minutes(
    trips: list[Trip],
    demand: list[DemandRecord],
    demand_results: list[DemandResult],
    departure_overrides: dict[str, int] | None = None,
    bus_overrides: dict[str, str] | None = None,
) -> tuple[float, int]:
    """
    Compute total waiting minutes and served passenger count.

    Waiting time per demand record served by a trip:
        wait = max(0, trip_departure_min - demand.time_window_start_min)

    Returns:
        (total_waiting_minutes, total_served_passengers)
    """
    departure_overrides = departure_overrides or {}
    result_map = {r.trip_id: r for r in demand_results}
    total_wait = 0.0
    total_served = 0

    for trip in trips:
        dep = departure_overrides.get(trip.trip_id, trip.scheduled_departure_min)
        result = result_map.get(trip.trip_id)
        if result is None:
            continue

        # Find demand records served by this trip
        matching = [
            d for d in demand
            if d.route_id == trip.route_id
            and d.time_window_start_min == dep
        ]
        # Served passengers for this trip
        served_here = result.total_served

        # Compute wait for each demand group proportionally
        total_demand_here = sum(d.passengers for d in matching)
        if total_demand_here == 0:
            continue

        for d in matching:
            wait = max(0.0, float(dep) - float(d.time_window_start_min))
            # Weight by fraction of demand that was actually served
            if total_demand_here > 0:
                served_fraction = min(d.passengers, served_here) / total_demand_here
                total_wait += wait * d.passengers * served_fraction

        total_served += served_here

    return total_wait, total_served


# ---------------------------------------------------------------------------
# Fleet utilisation
# ---------------------------------------------------------------------------

def compute_fleet_utilization(
    trips: list[Trip],
    buses: list[Bus],
    routes: list[Route_stub],
    scenario: Scenario_stub,
    departure_overrides: dict[str, int] | None = None,
    bus_overrides: dict[str, str] | None = None,
) -> float:
    """
    fleet_utilization = total_assigned_service_minutes /
                        total_available_bus_minutes   * 100

    Unavailable buses contribute 0 to both numerator and denominator.
    Returns a percentage in [0, 100].
    """
    departure_overrides = departure_overrides or {}
    bus_overrides = bus_overrides or {}

    from app.data.generate_dataset import _route_stop_arrivals
    from app.data.scenarios import get_trip_segment_delays

    route_map = {r.route_id: r for r in routes}

    # Numerator: sum of actual service time per assigned trip
    assigned_minutes = 0.0
    for trip in trips:
        bus_id = bus_overrides.get(trip.trip_id, trip.bus_id)
        bus = next((b for b in buses if b.bus_id == bus_id), None)
        if bus is None or bus.status == "unavailable":
            continue
        route = route_map.get(trip.route_id)
        if route is None:
            continue
        dep = float(departure_overrides.get(trip.trip_id, trip.scheduled_departure_min))
        route_dict = {
            "stop_ids": route.stop_ids,
            "segments": [
                {"from_stop_id": s.from_stop_id, "to_stop_id": s.to_stop_id,
                 "travel_time_min": s.travel_time_min}
                for s in route.segments
            ],
        }
        delays = get_trip_segment_delays(trip.trip_id, route_dict, scenario)
        arrivals = _route_stop_arrivals(route_dict, dep, delays)
        service_min = arrivals[-1][1] - arrivals[0][1]
        assigned_minutes += service_min

    # Denominator: sum of availability windows for available buses only
    available_minutes = sum(
        b.available_until_min - b.available_from_min
        for b in buses
        if b.status != "unavailable"
    )
    if available_minutes <= 0:
        return 0.0
    return round(min(100.0, (assigned_minutes / available_minutes) * 100.0), 2)


# ---------------------------------------------------------------------------
# ETA quality metrics
# ---------------------------------------------------------------------------

def compute_eta_quality(
    eta_predictions: dict[str, list[EtaPrediction]],
    ground_truth: list[GroundTruthArrival],
) -> EtaQualityMetrics:
    """
    Compute MAE and arrival-window coverage over evaluated trip-stop pairs.

    Only pairs that exist in BOTH eta_predictions AND ground_truth contribute
    to the denominator.  Unevaluated stops are never mixed in.
    """
    gt_map: dict[tuple[str, str], float] = {
        (gt.trip_id, gt.stop_id): gt.actual_arrival_min
        for gt in ground_truth
    }

    errors: list[float] = []
    in_window = 0

    for trip_id, preds in eta_predictions.items():
        for pred in preds:
            key = (pred.trip_id, pred.stop_id)
            actual = gt_map.get(key)
            if actual is None:
                continue
            errors.append(abs(pred.expected_arrival_min - actual))
            if pred.window_start_min <= actual <= pred.window_end_min:
                in_window += 1

    if not errors:
        return EtaQualityMetrics(
            mae_minutes=0.0,
            evaluated_count=0,
            window_coverage_pct=0.0,
        )

    mae = sum(errors) / len(errors)
    coverage = (in_window / len(errors)) * 100.0
    return EtaQualityMetrics(
        mae_minutes=round(mae, 4),
        evaluated_count=len(errors),
        window_coverage_pct=round(coverage, 2),
    )


# ---------------------------------------------------------------------------
# Objective cost
# ---------------------------------------------------------------------------

def compute_objective_cost(
    total_waiting_min: float,
    unmet_pax: int,
    missed_connections: int,
    weights: dict[str, float] | None = None,
) -> float:
    """
    cost = w_wait * total_waiting_minutes
         + w_unmet * unmet_passengers
         + w_conn  * missed_connections
    """
    w = {**DEFAULT_OBJECTIVE_WEIGHTS, **(weights or {})}
    return (
        w["waiting_time"] * total_waiting_min
        + w["unmet_passengers"] * unmet_pax
        + w["missed_connections"] * missed_connections
    )


# ---------------------------------------------------------------------------
# Full MetricSnapshot assembly
# ---------------------------------------------------------------------------

def build_metric_snapshot(
    trips: list[Trip],
    buses: list[Bus],
    routes: list,
    demand: list[DemandRecord],
    demand_results: list[DemandResult],
    scenario,
    eta_predictions: dict[str, list[EtaPrediction]] | None = None,
    ground_truth: list[GroundTruthArrival] | None = None,
    departure_overrides: dict[str, int] | None = None,
    bus_overrides: dict[str, str] | None = None,
    objective_weights: dict[str, float] | None = None,
) -> MetricSnapshot:
    """
    Assemble a complete MetricSnapshot for one plan.

    Args:
        trips, buses, routes, demand  : dataset entities.
        demand_results                : output of simulate_all_trips().
        scenario                      : active scenario definition.
        eta_predictions               : output of estimate_all_trips() (optional).
        ground_truth                  : ground-truth arrivals for ETA MAE (optional).
        departure_overrides           : planner departure shifts.
        bus_overrides                 : planner bus reassignments.
        objective_weights             : optional override of default weights.
    """
    departure_overrides = departure_overrides or {}
    bus_overrides = bus_overrides or {}

    total_wait, served = compute_total_waiting_minutes(
        trips, demand, demand_results, departure_overrides, bus_overrides
    )
    avg_wait = round(total_wait / served, 4) if served > 0 else 0.0
    unmet = total_unmet(demand_results)
    cap_violations = capacity_violation_count(demand_results)
    fleet_util = compute_fleet_utilization(
        trips, buses, routes, scenario, departure_overrides, bus_overrides
    )

    eta_quality: EtaQualityMetrics | None = None
    if eta_predictions and ground_truth:
        eta_quality = compute_eta_quality(eta_predictions, ground_truth)

    cost = compute_objective_cost(
        total_wait, unmet,
        missed_connections=0,   # not modelled in MVP
        weights=objective_weights,
    )

    return MetricSnapshot(
        avg_waiting_time_min=avg_wait,
        unmet_passengers=unmet,
        capacity_violations=cap_violations,
        fleet_utilization_pct=fleet_util,
        eta_mae_min=eta_quality.mae_minutes if eta_quality else None,
        eta_window_coverage_pct=(
            eta_quality.window_coverage_pct if eta_quality else None
        ),
        missed_connections="not_modelled",
        objective_cost=round(cost, 4),
    )


# ---------------------------------------------------------------------------
# Metric deltas (baseline vs proposed comparison)
# ---------------------------------------------------------------------------

def compute_metric_deltas(
    baseline: MetricSnapshot,
    proposed: MetricSnapshot,
) -> list[MetricDelta]:
    """
    Compute absolute and percentage deltas for each metric where lower is better.

    pct_change = (baseline - proposed) / baseline * 100
    When baseline is zero: pct_change = None (displayed as N/A).
    """
    def _delta(name: str, b, p) -> MetricDelta:
        if isinstance(b, (int, float)) and isinstance(p, (int, float)):
            abs_delta = round(float(p) - float(b), 4)
            if b != 0:
                pct = round((float(b) - float(p)) / abs(float(b)) * 100.0, 2)
            else:
                pct = None
        else:
            abs_delta = None
            pct = None
        return MetricDelta(
            metric=name,
            baseline=b,
            proposed=p,
            absolute_delta=abs_delta,
            pct_change=pct,
        )

    deltas = [
        _delta("avg_waiting_time_min",
               baseline.avg_waiting_time_min, proposed.avg_waiting_time_min),
        _delta("unmet_passengers",
               baseline.unmet_passengers, proposed.unmet_passengers),
        _delta("capacity_violations",
               baseline.capacity_violations, proposed.capacity_violations),
        _delta("fleet_utilization_pct",
               baseline.fleet_utilization_pct, proposed.fleet_utilization_pct),
        _delta("objective_cost",
               baseline.objective_cost, proposed.objective_cost),
    ]
    if baseline.eta_mae_min is not None and proposed.eta_mae_min is not None:
        deltas.append(_delta("eta_mae_min", baseline.eta_mae_min, proposed.eta_mae_min))
    return deltas


# Stub type annotations resolved at call time
Route_stub = object
Scenario_stub = object
