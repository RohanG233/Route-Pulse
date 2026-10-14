"""
Network and scenario routers.

GET /api/network   — return buses, stops, routes, trips, and dataset metadata.
GET /api/scenarios — list all supported scenario IDs, names, descriptions.
"""

from fastapi import APIRouter, Request

from app.config import RANDOM_SEED
from app.data.scenarios import list_scenarios
from app.schemas import NetworkResponse, ScenarioSummary

router = APIRouter()


@router.get(
    "/network",
    response_model=NetworkResponse,
    summary="Dataset network overview",
    description=(
        "Returns buses, stops, routes, trips, and dataset metadata. "
        "All data is synthetic and seeded with RANDOM_SEED."
    ),
)
async def get_network(request: Request) -> NetworkResponse:
    ds = request.app.state.dataset
    return NetworkResponse(
        buses=ds["buses"],
        stops=ds["stops"],
        routes=ds["routes"],
        trips=ds["trips"],
        dataset_seed=RANDOM_SEED,
        dataset_version="1.0.0",
    )


@router.get(
    "/scenarios",
    response_model=list[ScenarioSummary],
    summary="List scenarios",
    description="Returns all supported scenario IDs, names, and descriptions.",
)
async def get_scenarios() -> list[ScenarioSummary]:
    return [ScenarioSummary(**s) for s in list_scenarios()]


@router.get(
    "/scenarios/{scenario_id}/baseline",
    summary="Compute baseline for a scenario",
    description=(
        "Evaluates ETA, demand, alerts, and constraint checks for the "
        "baseline plan under the given scenario. Does not run the planner."
    ),
)
async def get_baseline(scenario_id: str, request: Request) -> dict:
    """
    Lightweight baseline endpoint: returns ETA map and demand results
    without running candidate search.  Used by the frontend to populate
    the route/schedule table before the user clicks Run Planning.
    """
    from app.data.scenarios import build_scenario_dataset, get_scenario
    from app.services.alert_service import generate_alerts
    from app.services.constraint_validator import validate_plan
    from app.services.demand_service import simulate_all_trips
    from app.services.eta_service import estimate_all_trips
    from app.schemas import min_to_hhmm
    from fastapi import HTTPException

    try:
        scenario = get_scenario(scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ds = request.app.state.dataset
    sc_ds = build_scenario_dataset(
        ds["stops"], ds["buses"], ds["routes"], ds["trips"],
        ds["demand"], ds["gps_traces"], ds["ground_truth"], scenario,
    )

    trips = sc_ds["trips"]
    buses = sc_ds["buses"]
    routes = sc_ds["routes"]
    demand = sc_ds["demand"]
    gps = sc_ds["gps_traces"]

    demand_results = simulate_all_trips(trips, routes, buses, demand)
    eta_map = estimate_all_trips(trips, routes, gps, scenario)
    validation = validate_plan(trips, buses, routes, demand, demand_results, scenario)
    alerts = generate_alerts(trips, routes, buses, demand_results, eta_map, scenario)

    # Serialize ETA predictions (convert float minutes to HH:MM for display)
    eta_out = {}
    for trip_id, preds in eta_map.items():
        eta_out[trip_id] = [
            {
                "stop_id": p.stop_id,
                "expected_arrival_min": p.expected_arrival_min,
                "expected_arrival_hhmm": min_to_hhmm(int(p.expected_arrival_min)),
                "window_start_min": p.window_start_min,
                "window_end_min": p.window_end_min,
                "estimate_source": p.estimate_source,
                "assumptions": p.assumptions,
            }
            for p in preds
        ]

    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario.name,
        "validation": validation.model_dump(),
        "alerts": [a.to_dict() for a in alerts],
        "demand_results": [r.model_dump() for r in demand_results],
        "eta_predictions": eta_out,
    }
