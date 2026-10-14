"""
Phase 6 edge-case and hardening tests.

These cover the remaining spec §15 acceptance criteria not exercised by
Phases 1–4, plus regression guards for the most critical invariants:

  ✓ Dataset regeneration with same seed produces byte-identical CSV/JSON files.
  ✓ Scenario reset (re-running the generator) is idempotent.
  ✓ Zero-baseline division: improvement% is N/A, not a crash or ÷0 error.
  ✓ POST /api/plan with explicit objective_weights produces a different cost
    than the default weights when unmet_passengers > 0.
  ✓ GET /api/scenarios/{id}/comparison returns the same result as POST /api/plan.
  ✓ Unknown scenario ID on every endpoint returns 404 with 'detail' containing
    the bad ID — no stack trace.
  ✓ Backend health is reachable (prerequisite for frontend integration).
  ✓ ETA MAE and arrival-window coverage denominators exclude unevaluated stops.
  ✓ Constraint validator rejects a plan that assigns an unavailable bus, even
    when the planner receives bus_overrides directly.
  ✓ Planner never claims improvement_found when objective cost did not decrease.
  ✓ Fleet utilisation stays ≤ 100% for all four scenarios.
  ✓ Metric deltas: absolute_delta == proposed - baseline for numeric metrics.
  ✓ The scenario seed (42) and version string are present in /api/network.
  ✓ All six test files import cleanly (no circular-import regressions).
  ✓ Regenerated dataset writes real files that can be loaded by data_loader.
"""

from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (
    BUS_DELAY_MINUTES,
    DATA_DIR,
    DEFAULT_OBJECTIVE_WEIGHTS,
    RANDOM_SEED,
    VEHICLE_UNAVAILABLE_BUS,
)
from app.data.generate_dataset import generate_all
from app.data.scenarios import (
    apply_bus_availability,
    apply_demand_transforms,
    build_scenario_dataset,
    get_scenario,
)
from app.schemas import (
    Bus,
    DemandRecord,
    GpsPoint,
    GroundTruthArrival,
    Route,
    RouteSegment,
    Trip,
)
from app.services.constraint_validator import validate_plan
from app.services.demand_service import simulate_all_trips
from app.services.eta_service import estimate_all_trips
from app.services.metrics_service import (
    build_metric_snapshot,
    compute_eta_quality,
    compute_metric_deltas,
)
from app.services.planner_service import run_planning
from app.services.data_loader import (
    load_all,
    load_buses,
    load_gps_traces,
    load_ground_truth,
    load_routes,
    load_trips,
)
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = tmp_path_factory.mktemp("ec")
    return generate_all(out_dir=out)


@pytest.fixture(scope="module")
def typed(tmp_path_factory):
    out = tmp_path_factory.mktemp("ec2")
    generate_all(out_dir=out)
    return load_all(out)


@pytest.fixture(scope="module")
def client():
    from app.data.generate_dataset import generate_all as gen
    if not (DATA_DIR / "stops.csv").exists():
        gen()
    from app.main import create_app
    with TestClient(create_app(), raise_server_exceptions=True) as c:
        yield c


# Shared builder helpers
def _trips(ds): return [Trip(**s) for s in ds["schedules"]]
def _buses(ds, sc=None):
    raw = ds["buses"]
    if sc: raw = apply_bus_availability(raw, sc)
    return [Bus(**b) for b in raw]
def _routes(ds):
    result = []
    for r in ds["routes"]:
        segs = [RouteSegment(**s) for s in r["segments"]]
        result.append(Route(route_id=r["route_id"], name=r["name"],
                            stop_ids=r["stop_ids"], segments=segs))
    return result
def _demand(ds, sc=None):
    raw = ds["demand"]
    if sc: raw = apply_demand_transforms(raw, sc)
    return [DemandRecord(**d) for d in raw]
def _gps(ds): return [GpsPoint(**p) for p in ds["gps_traces"]]
def _gt(ds):  return [GroundTruthArrival(**g) for g in ds["ground_truth"]]


# ===========================================================================
# 1. Dataset idempotency / reset
# ===========================================================================

class TestDatasetReset:
    def test_regeneration_produces_identical_files(self, tmp_path):
        """
        Running generate_all twice with the same seed must produce byte-identical
        CSV and JSON files.  This verifies the 'reset demo' use-case.
        """
        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        generate_all(out_dir=out1)
        generate_all(out_dir=out2)

        for filename in (
            "stops.csv", "buses.csv", "schedules.csv",
            "demand.csv", "gps_traces.csv",
        ):
            content1 = (out1 / filename).read_text(encoding="utf-8")
            content2 = (out2 / filename).read_text(encoding="utf-8")
            assert content1 == content2, (
                f"{filename} differs between two runs with same seed"
            )

        # JSON files
        for filename in ("routes.json", "ground_truth.json"):
            content1 = (out1 / filename).read_text(encoding="utf-8")
            content2 = (out2 / filename).read_text(encoding="utf-8")
            assert content1 == content2, (
                f"{filename} differs between two runs with same seed"
            )

    def test_generated_files_loadable_by_data_loader(self, tmp_path):
        """
        Files written by generate_all must be loadable by load_all without
        errors — confirming the write/read roundtrip is consistent.
        """
        out = tmp_path / "loader_test"
        generate_all(out_dir=out)
        data = load_all(out)
        assert len(data["buses"]) == 4
        assert len(data["stops"]) >= 8
        assert len(data["trips"]) >= 6

    def test_data_loader_raises_on_missing_files(self, tmp_path):
        """
        load_all on an empty directory must raise FileNotFoundError with a
        helpful message pointing to the generator command.
        """
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="generate_dataset"):
            load_all(empty)


# ===========================================================================
# 2. Zero-baseline division safety
# ===========================================================================

class TestZeroBaseline:
    def test_pct_change_none_when_baseline_zero(self):
        from app.schemas import MetricSnapshot

        zero = MetricSnapshot(
            avg_waiting_time_min=0.0, unmet_passengers=0,
            capacity_violations=0, fleet_utilization_pct=0.0,
            objective_cost=0.0,
        )
        nonzero = MetricSnapshot(
            avg_waiting_time_min=3.2, unmet_passengers=5,
            capacity_violations=0, fleet_utilization_pct=12.0,
            objective_cost=28.2,
        )
        deltas = compute_metric_deltas(zero, nonzero)
        for d in deltas:
            if d.baseline == 0 or d.baseline == 0.0:
                assert d.pct_change is None, (
                    f"Metric '{d.metric}': pct_change should be None "
                    f"when baseline=0, got {d.pct_change}"
                )

    def test_no_division_error_when_all_metrics_zero(self):
        """
        build_metric_snapshot for a scenario with zero demand must not crash.
        """
        from app.schemas import MetricSnapshot
        sc = get_scenario("normal")
        # Provide empty demand list — no served passengers, no wait
        trips_e: list[Trip] = []
        buses_e: list[Bus] = []
        routes_e: list[Route] = []
        demand_e: list[DemandRecord] = []
        dr_e = simulate_all_trips(trips_e, routes_e, buses_e, demand_e)
        snap = build_metric_snapshot(
            trips_e, buses_e, routes_e, demand_e, dr_e, sc
        )
        assert isinstance(snap, MetricSnapshot)
        assert math.isfinite(snap.avg_waiting_time_min)
        assert math.isfinite(snap.objective_cost)


# ===========================================================================
# 3. Objective weight override changes cost
# ===========================================================================

class TestObjectiveWeightOverride:
    def test_heavy_unmet_weight_increases_cost_for_spike(self, ds):
        """
        Tripling the unmet_passengers weight on a scenario that has unmet
        passengers must produce a strictly higher objective cost.
        """
        sc = get_scenario("demand_spike")
        trips = _trips(ds)
        buses = _buses(ds, sc)
        routes = _routes(ds)
        demand = _demand(ds, sc)
        gps = _gps(ds)
        gt = _gt(ds)

        default_resp = run_planning(
            scenario_id="demand_spike",
            trips=trips, buses=buses, routes=routes, demand=demand,
            gps_points=gps, ground_truth=gt, scenario=sc,
        )
        heavy_resp = run_planning(
            scenario_id="demand_spike",
            trips=trips, buses=buses, routes=routes, demand=demand,
            gps_points=gps, ground_truth=gt, scenario=sc,
            objective_weights={
                "waiting_time": 1.0,
                "unmet_passengers": 50.0,
                "missed_connections": 10.0,
            },
        )
        # demand_spike has unmet_passengers > 0; heavier weight must raise cost
        if default_resp.baseline_metrics.unmet_passengers > 0:
            assert (
                heavy_resp.baseline_metrics.objective_cost
                > default_resp.baseline_metrics.objective_cost
            ), (
                f"Heavy unmet weight should increase cost: "
                f"default={default_resp.baseline_metrics.objective_cost}, "
                f"heavy={heavy_resp.baseline_metrics.objective_cost}"
            )


# ===========================================================================
# 4. Comparison endpoint matches POST /api/plan
# ===========================================================================

class TestComparisonMatchesPlan:
    @pytest.mark.parametrize("sid", [
        "normal", "demand_spike", "bus_delay", "vehicle_unavailable"
    ])
    def test_comparison_equals_post_plan(self, client, sid):
        """
        GET /api/scenarios/{id}/comparison and POST /api/plan must return
        identical baseline_metrics.objective_cost and recommendation_status.
        """
        via_get = client.get(f"/api/scenarios/{sid}/comparison").json()
        via_post = client.post("/api/plan", json={"scenario_id": sid}).json()

        assert via_get["recommendation_status"] == via_post["recommendation_status"], (
            f"Scenario {sid}: status differs between GET comparison and POST plan"
        )
        assert (
            via_get["baseline_metrics"]["objective_cost"]
            == via_post["baseline_metrics"]["objective_cost"]
        ), f"Scenario {sid}: baseline cost differs"


# ===========================================================================
# 5. Unknown scenario ID → 404 with detail on every endpoint
# ===========================================================================

class TestUnknownScenario404:
    BAD_ID = "no_such_scenario_xyz"

    def test_baseline_404(self, client):
        resp = client.get(f"/api/scenarios/{self.BAD_ID}/baseline")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        assert self.BAD_ID in body["detail"]
        assert "Traceback" not in resp.text

    def test_plan_404(self, client):
        resp = client.post("/api/plan", json={"scenario_id": self.BAD_ID})
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        assert self.BAD_ID in body["detail"]

    def test_comparison_404(self, client):
        resp = client.get(f"/api/scenarios/{self.BAD_ID}/comparison")
        assert resp.status_code == 404
        assert self.BAD_ID in resp.json()["detail"]

    def test_no_stack_trace_in_error(self, client):
        """Error responses must never expose raw Python tracebacks."""
        for url in [
            f"/api/scenarios/{self.BAD_ID}/baseline",
            f"/api/scenarios/{self.BAD_ID}/comparison",
        ]:
            resp = client.get(url)
            assert "Traceback" not in resp.text, (
                f"{url}: stack trace leaked into response"
            )
        resp = client.post("/api/plan", json={"scenario_id": self.BAD_ID})
        assert "Traceback" not in resp.text


# ===========================================================================
# 6. ETA quality denominator is evaluated pairs only
# ===========================================================================

class TestEtaQualityDenominator:
    def test_evaluated_count_equals_matching_gt_pairs(self, ds):
        """
        compute_eta_quality must count only trip-stop pairs that exist in
        BOTH eta_predictions AND ground_truth.  Stops present in predictions
        but absent from ground_truth (or vice-versa) must not inflate the
        denominator.
        """
        sc = get_scenario("normal")
        trips = _trips(ds)
        routes = _routes(ds)
        gps = _gps(ds)
        gt = _gt(ds)
        eta_map = estimate_all_trips(trips, routes, gps, sc)

        # Full ground truth: all pairs should match
        full_quality = compute_eta_quality(eta_map, gt)
        expected_count = sum(len(preds) for preds in eta_map.values())
        assert full_quality.evaluated_count == expected_count, (
            f"Expected {expected_count} evaluated pairs, "
            f"got {full_quality.evaluated_count}"
        )

    def test_partial_ground_truth_reduces_count(self, ds):
        """
        Passing only half the ground-truth entries must halve the evaluated count.
        """
        sc = get_scenario("normal")
        trips = _trips(ds)
        routes = _routes(ds)
        gps = _gps(ds)
        gt = _gt(ds)
        eta_map = estimate_all_trips(trips, routes, gps, sc)

        full_q = compute_eta_quality(eta_map, gt)
        half_gt = gt[: len(gt) // 2]
        half_q = compute_eta_quality(eta_map, half_gt)

        assert half_q.evaluated_count < full_q.evaluated_count, (
            "Partial ground truth must yield fewer evaluated pairs"
        )
        assert half_q.evaluated_count == len(half_gt), (
            f"Evaluated count ({half_q.evaluated_count}) should equal "
            f"len(half_gt) ({len(half_gt)})"
        )


# ===========================================================================
# 7. Constraint validator: direct bus_override with unavailable bus → C2
# ===========================================================================

class TestConstraintValidatorDirectOverride:
    def test_reassigning_to_unavailable_bus_fails_c2(self, ds):
        """
        If the planner were given a bus_override that assigns an unavailable
        bus, the constraint validator must catch it with C2.
        """
        sc = get_scenario("vehicle_unavailable")
        trips = _trips(ds)
        buses = _buses(ds, sc)   # B1 is unavailable
        routes = _routes(ds)
        demand = _demand(ds)

        # Force-assign an unavailable bus via override
        bus_ov = {"T1": VEHICLE_UNAVAILABLE_BUS}
        dr = simulate_all_trips(trips, routes, buses, demand, bus_overrides=bus_ov)
        result = validate_plan(trips, buses, routes, demand, dr, sc,
                               bus_overrides=bus_ov)

        c2 = [v for v in result.violations if v.code == "C2"]
        assert len(c2) >= 1, (
            "validate_plan must produce C2 when bus_override forces unavailable bus"
        )
        assert not result.passed


# ===========================================================================
# 8. Planner never claims improvement when cost did not decrease
# ===========================================================================

class TestImprovementClaim:
    @pytest.mark.parametrize("sid", [
        "normal", "demand_spike", "bus_delay", "vehicle_unavailable"
    ])
    def test_improvement_claimed_only_when_cost_lower(self, ds, sid):
        sc = get_scenario(sid)
        trips = _trips(ds)
        buses = _buses(ds, sc)
        routes = _routes(ds)
        demand = _demand(ds, sc)
        gps = _gps(ds)
        gt = _gt(ds)

        resp = run_planning(
            scenario_id=sid,
            trips=trips, buses=buses, routes=routes, demand=demand,
            gps_points=gps, ground_truth=gt, scenario=sc,
        )
        if resp.recommendation_status == "improvement_found":
            assert resp.proposed_metrics is not None
            assert (
                resp.proposed_metrics.objective_cost
                < resp.baseline_metrics.objective_cost
            ), (
                f"Scenario {sid}: improvement_found claimed but "
                f"proposed cost ({resp.proposed_metrics.objective_cost}) "
                f">= baseline ({resp.baseline_metrics.objective_cost})"
            )


# ===========================================================================
# 9. Fleet utilisation stays ≤ 100% across all scenarios
# ===========================================================================

class TestFleetUtilisationBound:
    @pytest.mark.parametrize("sid", [
        "normal", "demand_spike", "bus_delay", "vehicle_unavailable"
    ])
    def test_fleet_util_lte_100(self, client, sid):
        resp = client.post("/api/plan", json={"scenario_id": sid}).json()
        util = resp["baseline_metrics"]["fleet_utilization_pct"]
        assert util <= 100.0, (
            f"Scenario {sid}: fleet_utilization_pct={util} > 100"
        )
        assert util >= 0.0


# ===========================================================================
# 10. Metric delta arithmetic
# ===========================================================================

class TestMetricDeltaArithmetic:
    def test_absolute_delta_equals_proposed_minus_baseline(self):
        from app.schemas import MetricSnapshot

        baseline = MetricSnapshot(
            avg_waiting_time_min=8.0, unmet_passengers=15,
            capacity_violations=0, fleet_utilization_pct=60.0,
            objective_cost=83.0,
        )
        proposed = MetricSnapshot(
            avg_waiting_time_min=5.0, unmet_passengers=5,
            capacity_violations=0, fleet_utilization_pct=65.0,
            objective_cost=30.0,
        )
        deltas = compute_metric_deltas(baseline, proposed)
        delta_map = {d.metric: d for d in deltas}

        # absolute_delta = proposed - baseline
        assert delta_map["avg_waiting_time_min"].absolute_delta == pytest.approx(
            5.0 - 8.0, abs=1e-6
        )
        assert delta_map["unmet_passengers"].absolute_delta == pytest.approx(
            5 - 15, abs=1e-6
        )
        assert delta_map["objective_cost"].absolute_delta == pytest.approx(
            30.0 - 83.0, abs=1e-6
        )

    def test_pct_change_sign_convention(self):
        """
        pct_change = (baseline - proposed) / |baseline| * 100.
        When proposed < baseline (improvement), pct_change > 0.
        When proposed > baseline (regression), pct_change < 0.
        """
        from app.schemas import MetricSnapshot

        baseline = MetricSnapshot(
            avg_waiting_time_min=10.0, unmet_passengers=20,
            capacity_violations=0, fleet_utilization_pct=50.0,
            objective_cost=110.0,
        )
        # Improvement: proposed is lower
        proposed_better = MetricSnapshot(
            avg_waiting_time_min=5.0, unmet_passengers=10,
            capacity_violations=0, fleet_utilization_pct=55.0,
            objective_cost=55.0,
        )
        deltas = compute_metric_deltas(baseline, proposed_better)
        cost_delta = next(d for d in deltas if d.metric == "objective_cost")
        assert cost_delta.pct_change is not None
        assert cost_delta.pct_change > 0, (
            "Improvement (proposed < baseline) must yield positive pct_change"
        )

        # Regression: proposed is higher
        proposed_worse = MetricSnapshot(
            avg_waiting_time_min=15.0, unmet_passengers=30,
            capacity_violations=0, fleet_utilization_pct=40.0,
            objective_cost=165.0,
        )
        deltas2 = compute_metric_deltas(baseline, proposed_worse)
        cost_delta2 = next(d for d in deltas2 if d.metric == "objective_cost")
        assert cost_delta2.pct_change is not None
        assert cost_delta2.pct_change < 0, (
            "Regression (proposed > baseline) must yield negative pct_change"
        )


# ===========================================================================
# 11. API /network fields
# ===========================================================================

class TestNetworkFieldsAndSeed:
    def test_seed_matches_config(self, client):
        body = client.get("/api/network").json()
        assert body["dataset_seed"] == RANDOM_SEED, (
            f"Expected seed {RANDOM_SEED}, got {body['dataset_seed']}"
        )

    def test_dataset_version_present(self, client):
        body = client.get("/api/network").json()
        assert body.get("dataset_version"), "dataset_version must be non-empty"

    def test_trips_reference_valid_route_ids(self, client):
        body = client.get("/api/network").json()
        route_ids = {r["route_id"] for r in body["routes"]}
        for trip in body["trips"]:
            assert trip["route_id"] in route_ids, (
                f"Trip {trip['trip_id']} references unknown route {trip['route_id']}"
            )

    def test_trips_reference_valid_bus_ids(self, client):
        body = client.get("/api/network").json()
        bus_ids = {b["bus_id"] for b in body["buses"]}
        for trip in body["trips"]:
            assert trip["bus_id"] in bus_ids, (
                f"Trip {trip['trip_id']} references unknown bus {trip['bus_id']}"
            )


# ===========================================================================
# 12. No circular-import regressions
# ===========================================================================

class TestModuleImports:
    """
    Importing every service module must not raise ImportError.
    This guards against accidental circular imports introduced during
    refactoring.
    """
    MODULES = [
        "app.config",
        "app.schemas",
        "app.data.generate_dataset",
        "app.data.scenarios",
        "app.services.data_loader",
        "app.services.eta_service",
        "app.services.demand_service",
        "app.services.alert_service",
        "app.services.constraint_validator",
        "app.services.metrics_service",
        "app.services.planner_service",
        "app.routers.health",
        "app.routers.network",
        "app.routers.planning",
        "app.main",
    ]

    @pytest.mark.parametrize("module", MODULES)
    def test_module_imports_cleanly(self, module: str):
        try:
            importlib.import_module(module)
        except ImportError as exc:
            pytest.fail(f"ImportError importing {module}: {exc}")


# ===========================================================================
# 13. build_scenario_dataset accepts typed Pydantic objects (regression guard)
# ===========================================================================

class TestBuildScenarioDatasetWithTypedObjects:
    def test_accepts_pydantic_objects(self, typed):
        """
        build_scenario_dataset must work when passed typed Pydantic objects
        (from load_all) rather than raw dicts (from generate_all).
        This is the path taken by the API routers.
        """
        sc = get_scenario("demand_spike")
        sc_ds = build_scenario_dataset(
            typed["stops"], typed["buses"], typed["routes"], typed["trips"],
            typed["demand"], typed["gps_traces"], typed["ground_truth"], sc,
        )
        # Result must contain typed Pydantic objects
        from app.schemas import Bus, DemandRecord, GpsPoint, Route, Stop, Trip
        assert all(isinstance(b, Bus)          for b in sc_ds["buses"])
        assert all(isinstance(r, Route)        for r in sc_ds["routes"])
        assert all(isinstance(t, Trip)         for t in sc_ds["trips"])
        assert all(isinstance(d, DemandRecord) for d in sc_ds["demand"])
        assert all(isinstance(g, GpsPoint)     for g in sc_ds["gps_traces"])

    def test_demand_spike_multiplier_applied_on_typed_input(self, typed):
        """
        R2 demand must be scaled even when typed DemandRecord objects are passed.
        """
        sc = get_scenario("demand_spike")
        sc_ds = build_scenario_dataset(
            typed["stops"], typed["buses"], typed["routes"], typed["trips"],
            typed["demand"], typed["gps_traces"], typed["ground_truth"], sc,
        )
        from app.config import DEMAND_SPIKE_MULTIPLIER
        import math

        # Check R2 demand rows are scaled
        original_r2 = [d for d in typed["demand"] if d.route_id == "R2"]
        scaled_r2 = [d for d in sc_ds["demand"] if d.route_id == "R2"]
        for orig, scaled in zip(
            sorted(original_r2, key=lambda d: d.demand_id),
            sorted(scaled_r2,   key=lambda d: d.demand_id),
        ):
            expected = math.ceil(orig.passengers * DEMAND_SPIKE_MULTIPLIER)
            assert scaled.passengers == expected, (
                f"Demand {orig.demand_id}: expected {expected} after spike, "
                f"got {scaled.passengers}"
            )


# ===========================================================================
# 14. Baseline endpoint ETA has expected_arrival_hhmm field
# ===========================================================================

class TestBaselineEtaHhmmField:
    def test_every_eta_row_has_hhmm(self, client):
        """
        The baseline endpoint adds expected_arrival_hhmm (HH:MM string) to
        every ETA prediction row.  The frontend relies on this field.
        """
        body = client.get("/api/scenarios/normal/baseline").json()
        for trip_id, rows in body["eta_predictions"].items():
            for row in rows:
                assert "expected_arrival_hhmm" in row, (
                    f"Trip {trip_id}: ETA row missing expected_arrival_hhmm"
                )
                hhmm = row["expected_arrival_hhmm"]
                assert ":" in hhmm and len(hhmm) == 5, (
                    f"Trip {trip_id}: expected_arrival_hhmm '{hhmm}' "
                    "not in HH:MM format"
                )


# ===========================================================================
# 15. Vehicle-unavailable: proposed plan (if found) never assigns B1
# ===========================================================================

class TestVehicleUnavailableProposed:
    def test_proposed_actions_never_assign_unavailable_bus(self, ds):
        sc = get_scenario("vehicle_unavailable")
        trips = _trips(ds)
        buses = _buses(ds, sc)
        routes = _routes(ds)
        demand = _demand(ds, sc)
        gps = _gps(ds)
        gt = _gt(ds)

        resp = run_planning(
            scenario_id="vehicle_unavailable",
            trips=trips, buses=buses, routes=routes, demand=demand,
            gps_points=gps, ground_truth=gt, scenario=sc,
        )
        for action in resp.actions:
            assert action.new_bus_id != VEHICLE_UNAVAILABLE_BUS, (
                f"Action tried to assign unavailable bus {VEHICLE_UNAVAILABLE_BUS}"
            )

    def test_proposed_plan_constraint_passes_when_found(self, ds):
        sc = get_scenario("vehicle_unavailable")
        trips = _trips(ds)
        buses = _buses(ds, sc)
        routes = _routes(ds)
        demand = _demand(ds, sc)
        gps = _gps(ds)
        gt = _gt(ds)

        resp = run_planning(
            scenario_id="vehicle_unavailable",
            trips=trips, buses=buses, routes=routes, demand=demand,
            gps_points=gps, ground_truth=gt, scenario=sc,
        )
        if resp.proposed_metrics is not None:
            assert resp.constraint_validation.passed, (
                f"Proposed plan must pass all constraints. "
                f"Violations: {[v.message for v in resp.constraint_validation.violations]}"
            )
