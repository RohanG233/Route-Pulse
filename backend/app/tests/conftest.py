"""
pytest configuration and shared fixtures for the RoutePulse test suite.

Fixtures defined here are available to every test file without explicit import.
The session-scoped `generated_dir` fixture generates the dataset once per test
session, so the generator is never called more than once per `pytest` run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the backend/ directory is on sys.path so `from app.xxx import ...`
# works regardless of which directory pytest is launched from.
BACKEND_DIR = Path(__file__).resolve().parents[3]   # .../backend/
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Session-scoped dataset generation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def generated_dir(tmp_path_factory) -> Path:
    """
    Generate a fresh deterministic dataset into a temporary directory and
    return the path.  Shared across the entire test session so the generator
    runs exactly once.
    """
    from app.data.generate_dataset import generate_all

    out: Path = tmp_path_factory.mktemp("dataset_session")
    generate_all(out_dir=out)
    return out


@pytest.fixture(scope="session")
def base_dataset(generated_dir: Path) -> dict:
    """
    Raw dict dataset (as returned by generate_all) loaded from the session
    temp dir.  Available to every test that declares `base_dataset` as a
    parameter.
    """
    from app.data.generate_dataset import generate_all

    return generate_all(out_dir=generated_dir)


@pytest.fixture(scope="session")
def typed_dataset(generated_dir: Path) -> dict:
    """
    Fully-typed Pydantic dataset (as returned by data_loader.load_all)
    loaded from the session temp dir.
    """
    from app.services.data_loader import load_all

    return load_all(generated_dir)


# ---------------------------------------------------------------------------
# Scenario helpers available everywhere
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def normal_scenario():
    from app.data.scenarios import get_scenario
    return get_scenario("normal")


@pytest.fixture(scope="session")
def demand_spike_scenario():
    from app.data.scenarios import get_scenario
    return get_scenario("demand_spike")


@pytest.fixture(scope="session")
def bus_delay_scenario():
    from app.data.scenarios import get_scenario
    return get_scenario("bus_delay")


@pytest.fixture(scope="session")
def vehicle_unavailable_scenario():
    from app.data.scenarios import get_scenario
    return get_scenario("vehicle_unavailable")
