"""
Phase 1 dataset tests.

Covers every acceptance criterion from spec section 15 that is
testable at the data-generation level:

 ✓ Dataset generation is deterministic for the same seed.
 ✓ All referenced IDs exist and route/demand order is valid.
 ✓ GPS timestamps are non-decreasing per trip.
 ✓ Ground truth is NOT imported anywhere inside the ETA estimator module.
 ✓ Normal-scenario baseline has no capacity violations (no bus overloaded).
 ✓ Demand-spike scenario produces overloaded demand on R2 (unmet passengers
   are expected; this is a deliberate baseline characteristic).
 ✓ Vehicle-unavailable scenario excludes the named bus.
 ✓ Capacity values are positive integers.
 ✓ Demand passenger counts are non-negative.
 ✓ Scenario demand transforms are deterministic and correct.
"""

import importlib
import math
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Make sure imports resolve correctly when running from backend/ root
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[3]   # .../backend/
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (
    BUS_CAPACITIES,
    DEMAND_SPIKE_MULTIPLIER,
    RANDOM_SEED,
    SERVICE_WINDOW_END_MIN,
    SERVICE_WINDOW_START_MIN,
    VEHICLE_UNAVAILABLE_BUS,
)
from app.data.generate_dataset import (
    generate_all,
    generate_buses,
    generate_demand,
    generate_routes,
    generate_schedules,
    generate_stops,
    validate_dataset,
)
from app.data.scenarios import (
    SCENARIOS,
    apply_bus_availability,
    apply_demand_transforms,
    get_scenario,
    list_scenarios,
)


# ---------------------------------------------------------------------------
# Fixtures: generate dataset once per session (fast, pure Python)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_dataset(tmp_path_factory):
    """Generate the full dataset into a temp directory, return the data dicts."""
    out = tmp_path_factory.mktemp("generated")
    return generate_all(out_dir=out)


@pytest.fixture(scope="session")
def base_dataset_second_run(tmp_path_factory):
    """Second independent run for determinism checks."""
    out = tmp_path_factory.mktemp("generated2")
    return generate_all(out_dir=out)


# ===========================================================================
# 1. Determinism
# ===========================================================================

class TestDeterminism:
    def test_stops_identical(self, base_dataset, base_dataset_second_run):
        assert base_dataset["stops"] == base_dataset_second_run["stops"]

    def test_buses_identical(self, base_dataset, base_dataset_second_run):
        assert base_dataset["buses"] == base_dataset_second_run["buses"]

    def test_routes_identical(self, base_dataset, base_dataset_second_run):
        assert base_dataset["routes"] == base_dataset_second_run["routes"]

    def test_schedules_identical(self, base_dataset, base_dataset_second_run):
        assert base_dataset["schedules"] == base_dataset_second_run["schedules"]

    def test_demand_identical(self, base_dataset, base_dataset_second_run):
        assert base_dataset["demand"] == base_dataset_second_run["demand"]

    def test_gps_traces_identical(self, base_dataset, base_dataset_second_run):
        assert base_dataset["gps_traces"] == base_dataset_second_run["gps_traces"]

    def test_ground_truth_identical(self, base_dataset, base_dataset_second_run):
        assert base_dataset["ground_truth"] == base_dataset_second_run["ground_truth"]


# ===========================================================================
# 2. Minimum size requirements
# ===========================================================================

class TestMinimumSizes:
    def test_at_least_8_stops(self, base_dataset):
        assert len(base_dataset["stops"]) >= 8

    def test_exactly_4_buses(self, base_dataset):
        assert len(base_dataset["buses"]) == 4

    def test_exactly_3_routes(self, base_dataset):
        assert len(base_dataset["routes"]) == 3

    def test_at_least_6_trips(self, base_dataset):
        assert len(base_dataset["schedules"]) >= 6

    def test_demand_has_rows(self, base_dataset):
        assert len(base_dataset["demand"]) > 0

    def test_gps_has_points(self, base_dataset):
        assert len(base_dataset["gps_traces"]) > 0

    def test_ground_truth_has_entries(self, base_dataset):
        assert len(base_dataset["ground_truth"]) > 0


# ===========================================================================
# 3. ID integrity and referential validity
# ===========================================================================

class TestIdIntegrity:
    def test_stop_ids_unique(self, base_dataset):
        ids = [s["stop_id"] for s in base_dataset["stops"]]
        assert len(ids) == len(set(ids)), "Duplicate stop IDs found"

    def test_bus_ids_unique(self, base_dataset):
        ids = [b["bus_id"] for b in base_dataset["buses"]]
        assert len(ids) == len(set(ids)), "Duplicate bus IDs found"

    def test_route_ids_unique(self, base_dataset):
        ids = [r["route_id"] for r in base_dataset["routes"]]
        assert len(ids) == len(set(ids)), "Duplicate route IDs found"

    def test_trip_ids_unique(self, base_dataset):
        ids = [s["trip_id"] for s in base_dataset["schedules"]]
        assert len(ids) == len(set(ids)), "Duplicate trip IDs found"

    def test_schedule_routes_exist(self, base_dataset):
        route_ids = {r["route_id"] for r in base_dataset["routes"]}
        for trip in base_dataset["schedules"]:
            assert trip["route_id"] in route_ids, (
                f"Trip {trip['trip_id']} references unknown route {trip['route_id']}"
            )

    def test_schedule_buses_exist(self, base_dataset):
        bus_ids = {b["bus_id"] for b in base_dataset["buses"]}
        for trip in base_dataset["schedules"]:
            assert trip["bus_id"] in bus_ids, (
                f"Trip {trip['trip_id']} references unknown bus {trip['bus_id']}"
            )

    def test_demand_routes_exist(self, base_dataset):
        route_ids = {r["route_id"] for r in base_dataset["routes"]}
        for d in base_dataset["demand"]:
            assert d["route_id"] in route_ids

    def test_demand_stops_exist(self, base_dataset):
        stop_ids = {s["stop_id"] for s in base_dataset["stops"]}
        for d in base_dataset["demand"]:
            assert d["boarding_stop_id"] in stop_ids
            assert d["alighting_stop_id"] in stop_ids

    def test_demand_od_order_valid(self, base_dataset):
        """Boarding stop must appear before alighting stop in route order."""
        route_stop_map = {
            r["route_id"]: r["stop_ids"] for r in base_dataset["routes"]
        }
        for d in base_dataset["demand"]:
            stops = route_stop_map[d["route_id"]]
            board_idx = stops.index(d["boarding_stop_id"])
            alight_idx = stops.index(d["alighting_stop_id"])
            assert board_idx < alight_idx, (
                f"Demand {d['demand_id']}: boarding ({d['boarding_stop_id']}) "
                f"not before alighting ({d['alighting_stop_id']})"
            )

    def test_validate_dataset_passes_cleanly(self, base_dataset):
        errors = validate_dataset(
            base_dataset["stops"],
            base_dataset["buses"],
            base_dataset["routes"],
            base_dataset["schedules"],
            base_dataset["demand"],
            base_dataset["gps_traces"],
        )
        assert errors == [], f"Validation errors:\n" + "\n".join(errors)


# ===========================================================================
# 4. Field value constraints
# ===========================================================================

class TestFieldConstraints:
    def test_bus_capacities_positive(self, base_dataset):
        for b in base_dataset["buses"]:
            assert b["capacity"] > 0, f"Bus {b['bus_id']} capacity not positive"

    def test_bus_capacities_match_config(self, base_dataset):
        caps = sorted(b["capacity"] for b in base_dataset["buses"])
        assert caps == sorted(BUS_CAPACITIES)

    def test_bus_availability_window_valid(self, base_dataset):
        for b in base_dataset["buses"]:
            assert b["available_until_min"] > b["available_from_min"]

    def test_demand_passengers_non_negative(self, base_dataset):
        for d in base_dataset["demand"]:
            assert d["passengers"] >= 0

    def test_route_segments_positive_travel_time(self, base_dataset):
        for r in base_dataset["routes"]:
            for seg in r["segments"]:
                assert seg["travel_time_min"] > 0

    def test_route_segment_count(self, base_dataset):
        for r in base_dataset["routes"]:
            assert len(r["segments"]) == len(r["stop_ids"]) - 1

    def test_trips_depart_in_service_window(self, base_dataset):
        for trip in base_dataset["schedules"]:
            dep = trip["scheduled_departure_min"]
            assert SERVICE_WINDOW_START_MIN <= dep < SERVICE_WINDOW_END_MIN, (
                f"Trip {trip['trip_id']} departure {dep} outside service window "
                f"[{SERVICE_WINDOW_START_MIN}, {SERVICE_WINDOW_END_MIN})"
            )


# ===========================================================================
# 5. GPS trace ordering
# ===========================================================================

class TestGpsOrdering:
    def test_timestamps_non_decreasing_per_trip(self, base_dataset):
        """
        GPS samples for each trip must have non-decreasing timestamps.
        (The generator sorts them; this verifies the sort held through write/return.)
        """
        from collections import defaultdict
        trip_pts: dict[str, list[float]] = defaultdict(list)
        for pt in base_dataset["gps_traces"]:
            if pt.get("trip_id"):
                trip_pts[pt["trip_id"]].append(pt["timestamp_min"])

        assert trip_pts, "No GPS traces with trip_id found"
        for tid, timestamps in trip_pts.items():
            for i in range(1, len(timestamps)):
                assert timestamps[i] >= timestamps[i - 1], (
                    f"Trip {tid}: timestamp decreased at index {i}: "
                    f"{timestamps[i - 1]} -> {timestamps[i]}"
                )

    def test_every_trip_has_gps_samples(self, base_dataset):
        """Every scheduled trip should have at least one GPS sample."""
        trip_ids_with_gps = {
            pt["trip_id"]
            for pt in base_dataset["gps_traces"]
            if pt.get("trip_id")
        }
        for trip in base_dataset["schedules"]:
            assert trip["trip_id"] in trip_ids_with_gps, (
                f"Trip {trip['trip_id']} has no GPS samples"
            )


# ===========================================================================
# 6. Ground truth isolation (spec §15: "Ground truth is not imported into
#    the ETA estimator")
# ===========================================================================

class TestGroundTruthIsolation:
    def test_eta_service_does_not_import_ground_truth(self):
        """
        Verify eta_service.py never imports or calls the ground-truth accessor.
        We check the import block specifically: scan for 'ground_truth' in
        import statements rather than scanning the whole file (which would
        flag docstrings explaining the isolation guarantee).
        """
        eta_module_path = (
            Path(__file__).resolve().parents[1] / "services" / "eta_service.py"
        )
        if not eta_module_path.exists():
            pytest.skip("eta_service.py not yet created (Phase 2)")

        source = eta_module_path.read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        import_block = "\n".join(import_lines)
        assert "ground_truth" not in import_block, (
            "eta_service.py must not import anything ground-truth-related"
        )


# ===========================================================================
# 7. Normal scenario: baseline has no capacity violations
# ===========================================================================

class TestNormalScenarioBaseline:
    def test_no_overload_in_normal_baseline(self, base_dataset):
        """
        For each trip in the normal scenario, the total passengers whose demand
        time_window_start_min exactly matches the trip's scheduled_departure_min
        must not exceed the assigned bus capacity.

        Demand windows are tied to specific departure windows (e.g. 07:00 demand
        boards the 07:00 trip, not the 08:00 trip). This verifies the intentional
        design: normal baseline has no capacity violations.
        """
        buses = {b["bus_id"]: b["capacity"] for b in base_dataset["buses"]}

        for trip in base_dataset["schedules"]:
            capacity = buses[trip["bus_id"]]
            dep = trip["scheduled_departure_min"]

            # Demand that matches this trip exactly: same route + same time window
            matching_demand = [
                d for d in base_dataset["demand"]
                if d["route_id"] == trip["route_id"]
                and d["time_window_start_min"] == dep
            ]

            total_boarding = sum(d["passengers"] for d in matching_demand)

            assert total_boarding <= capacity, (
                f"Normal baseline: trip {trip['trip_id']} (dep={dep}) has "
                f"{total_boarding} passengers but bus {trip['bus_id']} only "
                f"has {capacity} seats"
            )


# ===========================================================================
# 8. Demand-spike scenario: R2 becomes overloaded
# ===========================================================================

class TestDemandSpikeScenario:
    def test_demand_spike_multiplier_applied(self, base_dataset):
        """R2 demand rows should have their passengers multiplied."""
        scenario = get_scenario("demand_spike")
        original_r2 = [d for d in base_dataset["demand"] if d["route_id"] == "R2"]
        transformed = apply_demand_transforms(base_dataset["demand"], scenario)
        transformed_r2 = [d for d in transformed if d["route_id"] == "R2"]

        for orig, trans in zip(original_r2, transformed_r2):
            expected = math.ceil(orig["passengers"] * DEMAND_SPIKE_MULTIPLIER)
            assert trans["passengers"] == expected, (
                f"Demand {orig['demand_id']}: expected {expected} passengers "
                f"after spike, got {trans['passengers']}"
            )

    def test_non_r2_demand_unchanged(self, base_dataset):
        """Demand on routes other than R2 must not change in demand_spike."""
        scenario = get_scenario("demand_spike")
        transformed = apply_demand_transforms(base_dataset["demand"], scenario)
        orig_map = {d["demand_id"]: d for d in base_dataset["demand"]}
        for d in transformed:
            if d["route_id"] != "R2":
                assert d["passengers"] == orig_map[d["demand_id"]]["passengers"]

    def test_r2_demand_exceeds_largest_bus(self, base_dataset):
        """
        After the spike, total R2 demand at the 08:00 window should exceed
        B1's capacity (50), making the overload detectable.
        """
        scenario = get_scenario("demand_spike")
        transformed = apply_demand_transforms(base_dataset["demand"], scenario)

        r2_08_demand = sum(
            d["passengers"]
            for d in transformed
            if d["route_id"] == "R2"
            and d["time_window_start_min"] == SERVICE_WINDOW_START_MIN + 60
        )
        b1_capacity = next(
            b["capacity"] for b in base_dataset["buses"] if b["bus_id"] == "B1"
        )
        assert r2_08_demand > b1_capacity, (
            f"demand_spike R2 08:00 demand ({r2_08_demand}) should exceed "
            f"B1 capacity ({b1_capacity}) to make the scenario meaningful"
        )


# ===========================================================================
# 9. Vehicle-unavailable scenario
# ===========================================================================

class TestVehicleUnavailableScenario:
    def test_unavailable_bus_marked(self, base_dataset):
        scenario = get_scenario("vehicle_unavailable")
        transformed_buses = apply_bus_availability(base_dataset["buses"], scenario)
        bus_map = {b["bus_id"]: b for b in transformed_buses}

        assert bus_map[VEHICLE_UNAVAILABLE_BUS]["status"] == "unavailable", (
            f"Bus {VEHICLE_UNAVAILABLE_BUS} should be 'unavailable' in "
            "vehicle_unavailable scenario"
        )

    def test_other_buses_still_available(self, base_dataset):
        scenario = get_scenario("vehicle_unavailable")
        transformed_buses = apply_bus_availability(base_dataset["buses"], scenario)
        for b in transformed_buses:
            if b["bus_id"] != VEHICLE_UNAVAILABLE_BUS:
                assert b["status"] == "available", (
                    f"Bus {b['bus_id']} should remain available"
                )


# ===========================================================================
# 10. Scenario registry
# ===========================================================================

class TestScenarioRegistry:
    def test_all_four_scenarios_exist(self):
        assert set(SCENARIOS.keys()) == {
            "normal", "demand_spike", "bus_delay", "vehicle_unavailable"
        }

    def test_get_scenario_returns_correct_type(self):
        from app.schemas import ScenarioDefinition
        for sid in SCENARIOS:
            s = get_scenario(sid)
            assert isinstance(s, ScenarioDefinition)

    def test_unknown_scenario_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown scenario_id"):
            get_scenario("nonexistent_scenario")

    def test_list_scenarios_returns_all(self):
        summaries = list_scenarios()
        ids = {s["scenario_id"] for s in summaries}
        assert ids == set(SCENARIOS.keys())

    def test_scenario_demand_transform_is_deterministic(self, base_dataset):
        """Applying the same transform twice produces the same result."""
        scenario = get_scenario("demand_spike")
        run1 = apply_demand_transforms(base_dataset["demand"], scenario)
        run2 = apply_demand_transforms(base_dataset["demand"], scenario)
        assert run1 == run2
