"""GET /api/health — liveness check."""

from fastapi import APIRouter
from app.schemas import HealthResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns {status: 'ok'} when the backend is running.",
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
