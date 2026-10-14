"""
RoutePulse FastAPI application entry point.

Start the server:
    cd backend
    uvicorn app.main:app --reload --port 8000

The app loads the dataset once at startup into app.state so every request
reads from the same in-memory snapshot.  This is safe because the dataset
is read-only during the server lifetime — planning never mutates it.

If the generated dataset files are missing, the server fails at startup
with a clear error message rather than returning 500s on every request.

Environment variables are read from .env (if present) before any import
that touches app.config, so values set in .env are visible project-wide.
"""

from __future__ import annotations

# Load .env early — before config constants are evaluated.
# python-dotenv is optional; if not installed the app still works via
# environment variables set by the shell or the container runtime.
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)   # shell vars take precedence over .env
except ImportError:
    pass  # python-dotenv not installed — that's fine

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.data.generate_dataset import generate_all
from app.config import CORS_ORIGINS, DATA_DIR, LOG_LEVEL, RANDOM_SEED
from app.services.data_loader import load_all
from app.routers import health, network, planning


# ---------------------------------------------------------------------------
# Application lifespan: load dataset once at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Load (or auto-generate) the dataset before accepting requests.

    Auto-generation only happens when generated/ is empty so that a fresh
    clone works without a manual generate step.  The same seed is always
    used so results remain reproducible.
    """
    stops_file = DATA_DIR / "stops.csv"
    if not stops_file.exists():
        print(
            "[RoutePulse] Generated dataset not found — "
            "running generator (seed={})...".format(RANDOM_SEED)
        )
        generate_all()

    try:
        ds = load_all()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Failed to load dataset at startup: {exc}\n"
            "Run: cd backend && python -m app.data.generate_dataset"
        ) from exc

    # Store typed dataset lists in app.state for use by all routers
    app.state.dataset = {
        "stops":       ds["stops"],
        "buses":       ds["buses"],
        "routes":      ds["routes"],
        "trips":       ds["trips"],
        "demand":      ds["demand"],
        "gps_traces":  ds["gps_traces"],
        "ground_truth": ds["ground_truth"],
    }
    print(
        f"[RoutePulse] Dataset loaded — "
        f"{len(ds['stops'])} stops, {len(ds['buses'])} buses, "
        f"{len(ds['routes'])} routes, {len(ds['trips'])} trips."
    )
    yield
    # Nothing to clean up for an in-memory dataset


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="RoutePulse",
        description=(
            "Campus transport planning API. "
            "All data is synthetic and seeded for reproducibility."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS — configured via CORS_ORIGINS env var (see backend/.env.example)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers — all mounted under /api
    app.include_router(health.router,   prefix="/api", tags=["health"])
    app.include_router(network.router,  prefix="/api", tags=["network"])
    app.include_router(planning.router, prefix="/api", tags=["planning"])

    # ---------------------------------------------------------------------------
    # Global error handlers
    # ---------------------------------------------------------------------------

    @app.exception_handler(KeyError)
    async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        """Convert KeyError (unknown scenario ID etc.) to 404."""
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc).strip("'")},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        """Convert ValueError (bad input) to 422."""
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc)},
        )

    @app.exception_handler(FileNotFoundError)
    async def file_not_found_handler(
        request: Request, exc: FileNotFoundError
    ) -> JSONResponse:
        """Convert missing dataset files to 503 with a clear message."""
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Dataset files not found. "
                    "Run: cd backend && python -m app.data.generate_dataset"
                )
            },
        )

    return app


# Module-level app instance used by uvicorn
app = create_app()
