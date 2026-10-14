"""
Data loader for RoutePulse.

Reads the generated CSV/JSON files from app/data/generated/ and returns
typed Pydantic objects.  The loader is stateless; call load_all() to get
a fresh snapshot from disk.

If the generated files do not exist, raises FileNotFoundError with a hint
to run the generator first.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.schemas import (
    Bus,
    DemandRecord,
    GpsPoint,
    GroundTruthArrival,
    Route,
    RouteSegment,
    Stop,
    Trip,
)


# ---------------------------------------------------------------------------
# Individual loaders
# ---------------------------------------------------------------------------

def load_stops(data_dir: Path = DATA_DIR) -> list[Stop]:
    """Load stops.csv → list[Stop]."""
    path = data_dir / "stops.csv"
    _require(path)
    stops = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stops.append(
                Stop(
                    stop_id=row["stop_id"],
                    name=row["name"],
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                )
            )
    return stops


def load_buses(data_dir: Path = DATA_DIR) -> list[Bus]:
    """Load buses.csv → list[Bus]."""
    path = data_dir / "buses.csv"
    _require(path)
    buses = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            buses.append(
                Bus(
                    bus_id=row["bus_id"],
                    capacity=int(row["capacity"]),
                    available_from_min=int(row["available_from_min"]),
                    available_until_min=int(row["available_until_min"]),
                    status=row.get("status", "available"),
                )
            )
    return buses


def load_routes(data_dir: Path = DATA_DIR) -> list[Route]:
    """Load routes.json → list[Route]."""
    path = data_dir / "routes.json"
    _require(path)
    with open(path, encoding="utf-8") as f:
        raw: list[dict] = json.load(f)
    routes = []
    for r in raw:
        segments = [
            RouteSegment(
                from_stop_id=seg["from_stop_id"],
                to_stop_id=seg["to_stop_id"],
                travel_time_min=float(seg["travel_time_min"]),
            )
            for seg in r["segments"]
        ]
        routes.append(
            Route(
                route_id=r["route_id"],
                name=r["name"],
                stop_ids=r["stop_ids"],
                segments=segments,
            )
        )
    return routes


def load_trips(data_dir: Path = DATA_DIR) -> list[Trip]:
    """Load schedules.csv → list[Trip]."""
    path = data_dir / "schedules.csv"
    _require(path)
    trips = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            trips.append(
                Trip(
                    trip_id=row["trip_id"],
                    route_id=row["route_id"],
                    bus_id=row["bus_id"],
                    scheduled_departure_min=int(row["scheduled_departure_min"]),
                    permitted_departure_window_min=int(
                        row["permitted_departure_window_min"]
                    ),
                    service_date=row.get("service_date", "scenario_day"),
                )
            )
    return trips


def load_demand(data_dir: Path = DATA_DIR) -> list[DemandRecord]:
    """Load demand.csv → list[DemandRecord]."""
    path = data_dir / "demand.csv"
    _require(path)
    demand = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            demand.append(
                DemandRecord(
                    demand_id=row["demand_id"],
                    route_id=row["route_id"],
                    boarding_stop_id=row["boarding_stop_id"],
                    alighting_stop_id=row["alighting_stop_id"],
                    time_window_start_min=int(row["time_window_start_min"]),
                    passengers=int(row["passengers"]),
                )
            )
    return demand


def load_gps_traces(data_dir: Path = DATA_DIR) -> list[GpsPoint]:
    """Load gps_traces.csv → list[GpsPoint]."""
    path = data_dir / "gps_traces.csv"
    _require(path)
    points = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            points.append(
                GpsPoint(
                    trace_id=row["trace_id"],
                    bus_id=row["bus_id"],
                    timestamp_min=float(row["timestamp_min"]),
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    route_id=row.get("route_id") or None,
                    trip_id=row.get("trip_id") or None,
                )
            )
    return points


def load_ground_truth(data_dir: Path = DATA_DIR) -> list[GroundTruthArrival]:
    """
    Load ground_truth.json → list[GroundTruthArrival].

    IMPORTANT: This function must ONLY be called from evaluation/metrics code.
    It must never be imported or called inside the ETA estimator.
    """
    path = data_dir / "ground_truth.json"
    _require(path)
    with open(path, encoding="utf-8") as f:
        raw: list[dict] = json.load(f)
    return [
        GroundTruthArrival(
            trip_id=r["trip_id"],
            stop_id=r["stop_id"],
            actual_arrival_min=float(r["actual_arrival_min"]),
        )
        for r in raw
    ]


# ---------------------------------------------------------------------------
# Composite loader
# ---------------------------------------------------------------------------

def load_all(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """
    Load every dataset file and return a dict of typed lists.

    Keys: stops, buses, routes, trips, demand, gps_traces, ground_truth.
    Raises FileNotFoundError (via _require) if any file is missing.
    """
    return {
        "stops": load_stops(data_dir),
        "buses": load_buses(data_dir),
        "routes": load_routes(data_dir),
        "trips": load_trips(data_dir),
        "demand": load_demand(data_dir),
        "gps_traces": load_gps_traces(data_dir),
        "ground_truth": load_ground_truth(data_dir),
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _require(path: Path) -> None:
    """Raise a helpful FileNotFoundError if a dataset file is missing."""
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset file not found: {path}\n"
            "Run the generator first:\n"
            "    cd backend\n"
            "    python -m app.data.generate_dataset"
        )
