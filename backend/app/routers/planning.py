"""
Planning router.

POST /api/plan              — run planning for a scenario, return full PlanResponse.
GET  /api/scenarios/{id}/comparison — return the latest computed comparison
                                       (re-computes deterministically each call).
"""

from fastapi import APIRouter, HTTPException, Request

from app.data.scenarios import build_scenario_dataset, get_scenario
from app.schemas import PlanRequest, PlanResponse
from app.services.planner_service import run_planning

router = APIRouter()


def _resolve_scenario_dataset(scenario_id: str, app_state):
    """
    Load the base dataset from app state, apply scenario transforms,
    and return typed entities ready for planning.

    Raises HTTPException 404 for unknown scenario IDs.
    """
    try:
        scenario = get_scenario(scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ds = app_state.dataset
    sc_ds = build_scenario_dataset(
        ds["stops"], ds["buses"], ds["routes"], ds["trips"],
        ds["demand"], ds["gps_traces"], ds["ground_truth"], scenario,
    )
    return scenario, sc_ds


@router.post(
    "/plan",
    response_model=PlanResponse,
    summary="Run planning for a scenario",
    description=(
        "Evaluates baseline, searches for feasible improvements, validates "
        "every candidate against C1–C7, and returns a full PlanResponse. "
        "Accepts optional objective weight overrides."
    ),
)
async def post_plan(body: PlanRequest, request: Request) -> PlanResponse:
    """
    POST /api/plan

    Body: {"scenario_id": "demand_spike", "objective_weights": {...}}

    Returns a PlanResponse with baseline metrics, proposed plan (or
    no-improvement explanation), constraint validation, and metric deltas.
    """
    scenario, sc_ds = _resolve_scenario_dataset(
        body.scenario_id, request.app.state
    )

    return run_planning(
        scenario_id=body.scenario_id,
        trips=sc_ds["trips"],
        buses=sc_ds["buses"],
        routes=sc_ds["routes"],
        demand=sc_ds["demand"],
        gps_points=sc_ds["gps_traces"],
        ground_truth=sc_ds["ground_truth"],
        scenario=scenario,
        objective_weights=body.objective_weights or None,
    )


@router.get(
    "/scenarios/{scenario_id}/comparison",
    response_model=PlanResponse,
    summary="Compute scenario comparison",
    description=(
        "Re-computes the planning result deterministically for the given "
        "scenario and returns it. Equivalent to POST /api/plan with default "
        "objective weights. No hidden server state required."
    ),
)
async def get_comparison(scenario_id: str, request: Request) -> PlanResponse:
    """
    GET /api/scenarios/{scenario_id}/comparison

    Deterministically recomputes the plan comparison without requiring a
    prior POST /api/plan call.  Useful for the frontend's comparison panel.
    """
    scenario, sc_ds = _resolve_scenario_dataset(
        scenario_id, request.app.state
    )

    return run_planning(
        scenario_id=scenario_id,
        trips=sc_ds["trips"],
        buses=sc_ds["buses"],
        routes=sc_ds["routes"],
        demand=sc_ds["demand"],
        gps_points=sc_ds["gps_traces"],
        ground_truth=sc_ds["ground_truth"],
        scenario=scenario,
    )
