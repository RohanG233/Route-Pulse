"""
Phase 4 API contract tests.

Uses FastAPI's TestClient (backed by httpx) to call every endpoint and
verify the full HTTP contract — status codes, response shapes, error
handling, and computed (non-hardcoded) metric values.

Spec §15 criteria covered:
  ✓ GET /api/health returns {status: 'ok'}.
  ✓ GET /api/network returns buses, stops, routes, trips with correct counts.
  ✓ GET /api/scenarios lists all four scenario IDs.
  ✓ GET /api/scenarios/{id}/baseline returns ETA predictions and demand results.
  ✓ POST /api/plan returns baseline_metrics, constraint_validation, status.
  ✓ POST /api/plan with unknown scenario_id returns 4xx with detail message.
  ✓ POST /api/plan with malformed body returns 422.
  ✓ No hardcoded metrics: objective_cost differs between normal and demand_spike.
  ✓ vehicle_unavailable plan: no accepted proposed plan assigns B1.
  ✓ demand_spike baseline has unmet_passengers > 0.
  ✓ Every accepted proposed plan has constraint_validation.passed == True.
  ✓ GET /api/scenarios/{id}/comparison is deterministic across two calls.
  ✓ Frontend loads data (network endpoint reachable and non-empty).
  ✓ API shows computed metrics, not placeholders.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Generate dataset before importing the app so lifespan can find the files
from app.data.generate_dataset import generate_all
from app.config import DATA_DIR

# Ensure dataset exists in the real DATA_DIR before app startup
if not (DATA_DIR / "stops.csv").exists():
    generate_all()

from app.main import create_app


# ---------------------------------------------------------------------------
# Shared test client fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Single TestClient instance shared across all API tests."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ===========================================================================
# 1. Health endpoint
# ===========================================================================

class TestHealth:
    def test_status_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_body_has_status_ok(self, client):
        body = client.get("/api/health").json()
        assert body == {"status": "ok"}


# ===========================================================================
# 2. Network endpoint
# ===========================================================================

class TestNetwork:
    def test_status_200(self, client):
        assert client.get("/api/network").status_code == 200

    def test_has_required_top_level_keys(self, client):
        body = client.get("/api/network").json()
        for key in ("buses", "stops", "routes", "trips",
                    "dataset_seed", "dataset_version"):
            assert key in body, f"Missing key: {key}"

    def test_buses_count_matches_config(self, client):
        from app.config import NUM_BUSES
        body = client.get("/api/network").json()
        assert len(body["buses"]) == NUM_BUSES

    def test_stops_count_at_least_8(self, client):
        body = client.get("/api/network").json()
        assert len(body["stops"]) >= 8

    def test_routes_count_matches_config(self, client):
        body = client.get("/api/network").json()
        assert len(body["routes"]) == 3

    def test_trips_count_at_least_6(self, client):
        body = client.get("/api/network").json()
        assert len(body["trips"]) >= 6

    def test_dataset_seed_is_correct(self, client):
        from app.config import RANDOM_SEED
        body = client.get("/api/network").json()
        assert body["dataset_seed"] == RANDOM_SEED

    def test_bus_objects_have_required_fields(self, client):
        body = client.get("/api/network").json()
        for bus in body["buses"]:
            for field in ("bus_id", "capacity", "available_from_min",
                          "available_until_min", "status"):
                assert field in bus, f"Bus missing field: {field}"
            assert bus["capacity"] > 0

    def test_stop_objects_have_required_fields(self, client):
        body = client.get("/api/network").json()
        for stop in body["stops"]:
            for field in ("stop_id", "name", "latitude", "longitude"):
                assert field in stop

    def test_route_objects_have_segments(self, client):
        body = client.get("/api/network").json()
        for route in body["routes"]:
            assert "segments" in route
            assert len(route["segments"]) == len(route["stop_ids"]) - 1


# ===========================================================================
# 3. Scenarios list endpoint
# ===========================================================================

class TestScenariosList:
    def test_status_200(self, client):
        assert client.get("/api/scenarios").status_code == 200

    def test_returns_list(self, client):
        body = client.get("/api/scenarios").json()
        assert isinstance(body, list)

    def test_all_four_scenarios_present(self, client):
        body = client.get("/api/scenarios").json()
        ids = {s["scenario_id"] for s in body}
        assert ids == {"normal", "demand_spike", "bus_delay", "vehicle_unavailable"}

    def test_each_scenario_has_name_and_description(self, client):
        body = client.get("/api/scenarios").json()
        for s in body:
            assert s.get("name"), f"Scenario {s['scenario_id']} missing name"
            assert s.get("description"), f"Scenario {s['scenario_id']} missing description"


# ===========================================================================
# 4. Baseline endpoint
# ===========================================================================

class TestBaseline:
    def test_normal_baseline_200(self, client):
        assert client.get("/api/scenarios/normal/baseline").status_code == 200

    def test_unknown_scenario_returns_404(self, client):
        resp = client.get("/api/scenarios/nonexistent/baseline")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        assert "nonexistent" in body["detail"]

    def test_baseline_has_required_keys(self, client):
        body = client.get("/api/scenarios/normal/baseline").json()
        for key in ("scenario_id", "validation", "alerts",
                    "demand_results", "eta_predictions"):
            assert key in body, f"Missing key: {key}"

    def test_baseline_eta_predictions_non_empty(self, client):
        body = client.get("/api/scenarios/normal/baseline").json()
        assert len(body["eta_predictions"]) > 0

    def test_baseline_validation_passed_for_normal(self, client):
        body = client.get("/api/scenarios/normal/baseline").json()
        assert body["validation"]["passed"] is True, (
            f"Normal baseline should pass validation; violations: "
            f"{body['validation']['violations']}"
        )

    def test_demand_spike_baseline_has_alerts(self, client):
        body = client.get("/api/scenarios/demand_spike/baseline").json()
        alert_codes = [a["code"] for a in body["alerts"]]
        assert "UNMET_DEMAND" in alert_codes, (
            f"demand_spike baseline must have UNMET_DEMAND alerts; got: {alert_codes}"
        )

    def test_bus_delay_baseline_has_late_arrival_alerts(self, client):
        body = client.get("/api/scenarios/bus_delay/baseline").json()
        alert_codes = [a["code"] for a in body["alerts"]]
        assert "LATE_ARRIVAL" in alert_codes, (
            f"bus_delay baseline must have LATE_ARRIVAL alerts; got: {alert_codes}"
        )

    def test_vehicle_unavailable_baseline_has_bus_unavailable_alert(self, client):
        body = client.get("/api/scenarios/vehicle_unavailable/baseline").json()
        alert_codes = [a["code"] for a in body["alerts"]]
        assert "BUS_UNAVAILABLE" in alert_codes

    def test_eta_predictions_have_required_fields(self, client):
        body = client.get("/api/scenarios/normal/baseline").json()
        for trip_id, preds in body["eta_predictions"].items():
            for p in preds:
                for field in ("stop_id", "expected_arrival_min",
                              "window_start_min", "window_end_min",
                              "estimate_source"):
                    assert field in p, (
                        f"Trip {trip_id} prediction missing field: {field}"
                    )

    def test_all_four_baselines_return_200(self, client):
        for sid in ("normal", "demand_spike", "bus_delay", "vehicle_unavailable"):
            resp = client.get(f"/api/scenarios/{sid}/baseline")
            assert resp.status_code == 200, (
                f"Scenario {sid} baseline returned {resp.status_code}"
            )


# ===========================================================================
# 5. POST /api/plan
# ===========================================================================

class TestPostPlan:
    def test_normal_plan_200(self, client):
        resp = client.post("/api/plan", json={"scenario_id": "normal"})
        assert resp.status_code == 200

    def test_unknown_scenario_returns_404(self, client):
        resp = client.post("/api/plan", json={"scenario_id": "does_not_exist"})
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        assert "does_not_exist" in body["detail"]

    def test_missing_scenario_id_returns_422(self, client):
        resp = client.post("/api/plan", json={"objective_weights": {}})
        assert resp.status_code == 422

    def test_malformed_body_returns_422(self, client):
        resp = client.post("/api/plan", content="not json",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 422

    def test_plan_response_has_required_fields(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        required = (
            "scenario_id", "recommendation_status", "recommendation_summary",
            "actions", "constraint_validation", "baseline_metrics",
            "metric_deltas", "assumptions", "limitations",
        )
        for field in required:
            assert field in body, f"PlanResponse missing field: {field}"

    def test_recommendation_status_is_valid_literal(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        assert body["recommendation_status"] in (
            "improvement_found", "no_improvement", "infeasible_baseline"
        )

    def test_baseline_metrics_fields_present(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        bm = body["baseline_metrics"]
        for field in ("avg_waiting_time_min", "unmet_passengers",
                      "capacity_violations", "fleet_utilization_pct",
                      "objective_cost", "missed_connections"):
            assert field in bm, f"baseline_metrics missing: {field}"

    def test_missed_connections_not_modelled(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        assert body["baseline_metrics"]["missed_connections"] == "not_modelled"

    def test_objective_cost_is_number(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        cost = body["baseline_metrics"]["objective_cost"]
        assert isinstance(cost, (int, float))
        assert cost >= 0.0

    def test_constraint_validation_structure(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        cv = body["constraint_validation"]
        assert "passed" in cv
        assert "violations" in cv
        assert isinstance(cv["violations"], list)

    def test_normal_scenario_constraint_validation_passed(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        cv = body["constraint_validation"]
        assert cv["passed"] is True, (
            f"Normal scenario proposed plan must pass constraints. "
            f"Violations: {cv['violations']}"
        )

    def test_vehicle_unavailable_is_infeasible_baseline(self, client):
        body = client.post(
            "/api/plan", json={"scenario_id": "vehicle_unavailable"}
        ).json()
        assert body["recommendation_status"] == "infeasible_baseline"

    def test_demand_spike_has_unmet_passengers(self, client):
        body = client.post(
            "/api/plan", json={"scenario_id": "demand_spike"}
        ).json()
        assert body["baseline_metrics"]["unmet_passengers"] > 0

    def test_accepted_proposed_plan_passes_constraints(self, client):
        """Any accepted proposed plan must have zero hard-constraint violations."""
        for sid in ("normal", "demand_spike", "bus_delay", "vehicle_unavailable"):
            body = client.post("/api/plan", json={"scenario_id": sid}).json()
            if body.get("proposed_metrics") is not None:
                cv = body["constraint_validation"]
                assert cv["passed"] is True, (
                    f"Scenario {sid}: accepted proposed plan has violations: "
                    f"{cv['violations']}"
                )

    def test_proposed_plan_cost_lte_baseline_when_improvement_found(self, client):
        """If status is improvement_found, proposed cost must be ≤ baseline."""
        for sid in ("normal", "demand_spike", "bus_delay"):
            body = client.post("/api/plan", json={"scenario_id": sid}).json()
            if body["recommendation_status"] == "improvement_found":
                assert body["proposed_metrics"] is not None
                assert (
                    body["proposed_metrics"]["objective_cost"]
                    <= body["baseline_metrics"]["objective_cost"]
                ), (
                    f"Scenario {sid}: proposed cost "
                    f"({body['proposed_metrics']['objective_cost']}) > baseline "
                    f"({body['baseline_metrics']['objective_cost']})"
                )

    def test_vehicle_unavailable_proposed_plan_excludes_b1(self, client):
        from app.config import VEHICLE_UNAVAILABLE_BUS
        body = client.post(
            "/api/plan", json={"scenario_id": "vehicle_unavailable"}
        ).json()
        for action in body.get("actions", []):
            assert action.get("new_bus_id") != VEHICLE_UNAVAILABLE_BUS, (
                f"Proposed plan must not assign unavailable bus {VEHICLE_UNAVAILABLE_BUS}"
            )

    def test_metrics_not_hardcoded(self, client):
        """
        Objective costs for normal and demand_spike must differ — proves
        costs are computed from data, not returned as fixed constants.
        """
        normal = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        spike = client.post("/api/plan", json={"scenario_id": "demand_spike"}).json()
        assert (
            spike["baseline_metrics"]["objective_cost"]
            != normal["baseline_metrics"]["objective_cost"]
        ), "objective_cost must differ between scenarios (not hardcoded)"

    def test_assumptions_non_empty(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        assert isinstance(body["assumptions"], list)
        assert len(body["assumptions"]) > 0

    def test_limitations_non_empty(self, client):
        body = client.post("/api/plan", json={"scenario_id": "normal"}).json()
        assert isinstance(body["limitations"], list)
        assert len(body["limitations"]) > 0

    def test_objective_weights_override_accepted(self, client):
        """
        Custom objective weights must be accepted and change the cost.
        Heavy unmet-passenger weight on demand_spike should produce higher cost.
        """
        default = client.post(
            "/api/plan", json={"scenario_id": "demand_spike"}
        ).json()
        heavy = client.post(
            "/api/plan",
            json={
                "scenario_id": "demand_spike",
                "objective_weights": {
                    "waiting_time": 1.0,
                    "unmet_passengers": 100.0,
                    "missed_connections": 10.0,
                },
            },
        ).json()
        # Both must succeed
        assert default["baseline_metrics"]["objective_cost"] >= 0
        assert heavy["baseline_metrics"]["objective_cost"] >= 0
        # Higher unmet weight → higher cost on demand_spike (which has unmet pax)
        if default["baseline_metrics"]["unmet_passengers"] > 0:
            assert (
                heavy["baseline_metrics"]["objective_cost"]
                > default["baseline_metrics"]["objective_cost"]
            ), "Higher unmet_passengers weight must increase objective cost"

    def test_metric_deltas_present_and_structured(self, client):
        """metric_deltas must be a list; when improvement_found, list is non-empty."""
        body = client.post("/api/plan", json={"scenario_id": "demand_spike"}).json()
        assert isinstance(body["metric_deltas"], list)
        if body["recommendation_status"] == "improvement_found":
            assert len(body["metric_deltas"]) > 0
            for d in body["metric_deltas"]:
                assert "metric" in d
                assert "baseline" in d
                assert "proposed" in d


# ===========================================================================
# 6. GET /api/scenarios/{id}/comparison
# ===========================================================================

class TestComparison:
    def test_normal_comparison_200(self, client):
        assert client.get("/api/scenarios/normal/comparison").status_code == 200

    def test_unknown_scenario_returns_404(self, client):
        resp = client.get("/api/scenarios/unknown_id/comparison")
        assert resp.status_code == 404

    def test_comparison_is_deterministic(self, client):
        """Two GET calls with the same scenario must return identical costs."""
        run1 = client.get("/api/scenarios/demand_spike/comparison").json()
        run2 = client.get("/api/scenarios/demand_spike/comparison").json()
        assert (
            run1["baseline_metrics"]["objective_cost"]
            == run2["baseline_metrics"]["objective_cost"]
        ), "Comparison endpoint must be deterministic"
        assert run1["recommendation_status"] == run2["recommendation_status"]

    def test_comparison_matches_post_plan(self, client):
        """GET /comparison must return the same result as POST /plan."""
        via_get = client.get("/api/scenarios/normal/comparison").json()
        via_post = client.post(
            "/api/plan", json={"scenario_id": "normal"}
        ).json()
        assert (
            via_get["baseline_metrics"]["objective_cost"]
            == via_post["baseline_metrics"]["objective_cost"]
        )
        assert via_get["recommendation_status"] == via_post["recommendation_status"]

    def test_all_scenarios_return_valid_comparison(self, client):
        for sid in ("normal", "demand_spike", "bus_delay", "vehicle_unavailable"):
            resp = client.get(f"/api/scenarios/{sid}/comparison")
            assert resp.status_code == 200, f"Comparison failed for {sid}"
            body = resp.json()
            assert "baseline_metrics" in body
            assert "recommendation_status" in body


# ===========================================================================
# 7. API error handling
# ===========================================================================

class TestErrorHandling:
    def test_baseline_unknown_scenario_has_detail(self, client):
        resp = client.get("/api/scenarios/FAKE/baseline")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        # The detail must mention the bad ID — not a stack trace
        assert "FAKE" in body["detail"]

    def test_plan_unknown_scenario_has_detail(self, client):
        resp = client.post("/api/plan", json={"scenario_id": "FAKE"})
        assert resp.status_code == 404
        assert "FAKE" in resp.json()["detail"]

    def test_plan_extra_unknown_fields_accepted(self, client):
        """Pydantic v2 ignores extra fields by default; must not return 422."""
        resp = client.post(
            "/api/plan",
            json={"scenario_id": "normal", "unknown_extra_field": "ignored"},
        )
        assert resp.status_code == 200

    def test_api_does_not_expose_stack_traces(self, client):
        """Error responses must contain 'detail', not raw Python tracebacks."""
        resp = client.post("/api/plan", json={"scenario_id": "BAD_ID"})
        body_text = resp.text
        assert "Traceback" not in body_text
        assert "File " not in body_text

    def test_empty_scenario_id_returns_error(self, client):
        """An empty string scenario_id must not silently succeed."""
        resp = client.post("/api/plan", json={"scenario_id": ""})
        # Either 404 (not found) or 422 (validation) — not 200
        assert resp.status_code in (404, 422)
