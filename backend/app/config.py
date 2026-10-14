"""
Central configuration for RoutePulse.

Every value has a hardcoded default that matches the MVP spec.
For deployment, override via environment variables (see backend/.env.example).

Loading order: environment variable → hardcoded default.
No third-party config library is required; plain os.getenv keeps the
dependency footprint minimal.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent      # .../backend/
APP_DIR     = Path(__file__).resolve().parent             # .../backend/app/
DATA_DIR    = APP_DIR / "data" / "generated"              # generated CSV/JSON

# ---------------------------------------------------------------------------
# Server (read by the startup script / Dockerfile CMD)
# ---------------------------------------------------------------------------
HOST:     str = os.getenv("HOST",     "0.0.0.0")
PORT:     int = int(os.getenv("PORT", "8000"))
WORKERS:  int = int(os.getenv("WORKERS", "1"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")

# ---------------------------------------------------------------------------
# CORS origins (comma-separated in env; list in code)
# ---------------------------------------------------------------------------
_cors_env = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000,http://localhost:4173",
)
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
RANDOM_SEED: int = int(os.getenv("RANDOM_SEED", "42"))

# ---------------------------------------------------------------------------
# Dataset sizes (not configurable — MVP spec constants)
# ---------------------------------------------------------------------------
MIN_STOPS:  int = 8
NUM_BUSES:  int = 4
NUM_ROUTES: int = 3
MIN_TRIPS:  int = 6

# ---------------------------------------------------------------------------
# Service window (minutes from midnight)
# Morning service: 07:00 – 10:00
# ---------------------------------------------------------------------------
SERVICE_WINDOW_START_MIN: int = 7 * 60   # 420
SERVICE_WINDOW_END_MIN:   int = 10 * 60  # 600

# ---------------------------------------------------------------------------
# Bus capacities (seats): B1=50, B2=40, B3=30, B4=20
# ---------------------------------------------------------------------------
BUS_CAPACITIES: list[int] = [50, 40, 30, 20]

# ---------------------------------------------------------------------------
# Segment travel-time bounds used by the generator (minutes)
# ---------------------------------------------------------------------------
SEGMENT_TRAVEL_TIME_MIN: int = 3
SEGMENT_TRAVEL_TIME_MAX: int = 8

# ---------------------------------------------------------------------------
# GPS trace generation
# ---------------------------------------------------------------------------
GPS_INTERVAL_MIN:  int   = 2    # sample every N minutes of simulated trip time
GPS_NOISE_BOUND:   float = 0.3  # ± minutes bounded noise per sample

# ---------------------------------------------------------------------------
# ETA estimation
# ---------------------------------------------------------------------------
ETA_FALLBACK_WINDOW_MIN:  float = 2.0  # ± minutes when sample count is too low
MIN_SAMPLES_FOR_MEDIAN:   int   = 3    # minimum GPS samples to use median

# ---------------------------------------------------------------------------
# Planning / candidate search
# ---------------------------------------------------------------------------
DEPARTURE_SHIFT_STEP_MIN: int = 5   # departure-time candidate increment
MAX_DEPARTURE_SHIFT_MIN:  int = 15  # maximum shift in either direction

# Repositioning buffer between consecutive trips on the same bus (minutes)
REPOSITIONING_BUFFER_MIN: int = 10

# ---------------------------------------------------------------------------
# Objective weights — overridable via env and per-request body
# ---------------------------------------------------------------------------
DEFAULT_OBJECTIVE_WEIGHTS: dict[str, float] = {
    "waiting_time":       float(os.getenv("WEIGHT_WAITING_TIME",      "1.0")),
    "unmet_passengers":   float(os.getenv("WEIGHT_UNMET_PASSENGERS",  "5.0")),
    "missed_connections": float(os.getenv("WEIGHT_MISSED_CONNECTIONS","10.0")),
}

# ---------------------------------------------------------------------------
# Scenario parameters (fixed by MVP spec; documented here)
# ---------------------------------------------------------------------------
DEMAND_SPIKE_MULTIPLIER:  float = 1.8   # R2 demand multiplier in demand_spike
BUS_DELAY_MINUTES:        int   = 10    # delay added to T3 in bus_delay
VEHICLE_UNAVAILABLE_BUS:  str   = "B1"  # bus excluded in vehicle_unavailable
