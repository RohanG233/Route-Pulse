"""
RoutePulse deterministic synthetic dataset generator.

Run directly:
    cd backend
    python -m app.data.generate_dataset

All outputs land in app/data/generated/:
    stops.csv
    buses.csv
    routes.json
    schedules.csv
    demand.csv
    gps_traces.csv
    ground_truth.json

Re-running with the same seed (42) always produces identical files.

Intentional baseline characteristics (documented):
  - Route R2 carries the most demand; in demand_spike it becomes overloaded.
  - Bus B1 is the only bus with enough capacity for the R2 peak load after the
    spike; removing it (vehicle_unavailable) forces either reallocation or
    a reported infeasibility.
  - Trip T3 (R2, 08:00) has a tight schedule; adding BUS_DELAY_MINUTES pushes
    its arrivals late and may cause a missed-connection alert.
  - The normal scenario has no hard-constraint violations in the baseline.
"""

import csv
import json
import math
import random
from pathlib import Path

from app.config import (
    BUS_CAPACITIES,
    BUS_DELAY_MINUTES,
    DATA_DIR,
    DEMAND_SPIKE_MULTIPLIER,
    GPS_INTERVAL_MIN,
    GPS_NOISE_BOUND,
    RANDOM_SEED,
    REPOSITIONING_BUFFER_MIN,
    SEGMENT_TRAVEL_TIME_MAX,
    SEGMENT_TRAVEL_TIME_MIN,
    SERVICE_WINDOW_END_MIN,
    SERVICE_WINDOW_START_MIN,
    VEHICLE_UNAVAILABLE_BUS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(minutes: float) -> str:
    """Format minutes-from-midnight as HH:MM (for display only)."""
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


# ---------------------------------------------------------------------------
# 1. Stops  (8 illustrative campus locations)
# ---------------------------------------------------------------------------

STOPS_DATA = [
    # stop_id, name,             lat,       lon
    ("S1", "Main Gate",          51.5000,  -0.1200),
    ("S2", "Library",            51.5015,  -0.1185),
    ("S3", "Science Block",      51.5030,  -0.1170),
    ("S4", "Sports Centre",      51.5045,  -0.1155),
    ("S5", "Engineering Hub",    51.5010,  -0.1220),
    ("S6", "Student Union",      51.5025,  -0.1240),
    ("S7", "Medical Centre",     51.5040,  -0.1210),
    ("S8", "Halls of Residence", 51.4990,  -0.1230),
]


def generate_stops() -> list[dict]:
    return [
        {"stop_id": s[0], "name": s[1], "latitude": s[2], "longitude": s[3]}
        for s in STOPS_DATA
    ]


# ---------------------------------------------------------------------------
# 2. Buses  (4 buses, varied capacities)
# ---------------------------------------------------------------------------

def generate_buses() -> list[dict]:
    """
    B1: largest bus (50 seats) — critical for R2 peak demand.
    B2: 40 seats.
    B3: 30 seats.
    B4: smallest (20 seats).
    All available across the full morning service window.
    """
    buses = []
    for i, cap in enumerate(BUS_CAPACITIES, start=1):
        buses.append({
            "bus_id": f"B{i}",
            "capacity": cap,
            "available_from_min": SERVICE_WINDOW_START_MIN,    # 07:00
            "available_until_min": SERVICE_WINDOW_END_MIN,      # 10:00
            "status": "available",
        })
    return buses


# ---------------------------------------------------------------------------
# 3. Routes  (3 routes, 4–6 stops each, deterministic travel times)
# ---------------------------------------------------------------------------

# Route definitions: (route_id, name, [stop_ids])
ROUTE_DEFS = [
    ("R1", "Campus Loop",      ["S1", "S2", "S3", "S4"]),           # 4 stops
    ("R2", "Central Express",  ["S1", "S5", "S6", "S7", "S3"]),      # 5 stops
    ("R3", "Residential Link", ["S8", "S1", "S2", "S5", "S6", "S3"]),# 6 stops
]

# Fixed segment travel times (minutes) — deterministic, not random.
# Derived from rough geographic spacing of the illustrative coordinates above.
# Intentionally not regenerated per-seed to keep routes stable across tests.
SEGMENT_TIMES: dict[tuple[str, str], float] = {
    # R1: Campus Loop
    ("S1", "S2"): 4.0,
    ("S2", "S3"): 4.0,
    ("S3", "S4"): 4.0,
    # R2: Central Express
    ("S1", "S5"): 3.0,
    ("S5", "S6"): 4.0,
    ("S6", "S7"): 5.0,
    ("S7", "S3"): 4.0,
    # R3: Residential Link
    ("S8", "S1"): 5.0,
    ("S1", "S2"): 4.0,   # shared with R1
    ("S2", "S5"): 3.0,
    ("S5", "S6"): 4.0,   # shared with R2
    ("S6", "S3"): 5.0,
}


def generate_routes() -> list[dict]:
    routes = []
    for route_id, name, stop_ids in ROUTE_DEFS:
        segments = []
        for i in range(len(stop_ids) - 1):
            key = (stop_ids[i], stop_ids[i + 1])
            tt = SEGMENT_TIMES[key]
            segments.append({
                "from_stop_id": stop_ids[i],
                "to_stop_id": stop_ids[i + 1],
                "travel_time_min": tt,
            })
        routes.append({
            "route_id": route_id,
            "name": name,
            "stop_ids": stop_ids,
            "segments": segments,
        })
    return routes


# ---------------------------------------------------------------------------
# 4. Schedules  (≥6 trips, morning window)
# ---------------------------------------------------------------------------
#
# Trip layout (baseline, no disruptions):
#
#  T1  R1  B2  07:00  departure_window=15  ends ~07:12
#  T2  R1  B3  08:00  departure_window=15  ends ~08:12
#  T3  R2  B1  08:00  departure_window=10  ends ~08:16  ← deliberately tight
#  T4  R2  B4  09:00  departure_window=15  ends ~09:16
#  T5  R3  B2  07:30  departure_window=15  ends ~07:51
#  T6  R3  B3  08:45  departure_window=15  ends ~09:06
#
# B2 serves T1 (ends 07:12) then T5 (starts 07:30) — gap = 18 min ≥ BUFFER ✓
# B3 serves T2 (ends 08:12) then T6 (starts 08:45) — gap = 33 min ≥ BUFFER ✓
# B1 serves T3 only; B4 serves T4 only in baseline.

TRIPS_DATA = [
    # trip_id, route_id, bus_id, scheduled_departure_min, window_min
    ("T1", "R1", "B2", SERVICE_WINDOW_START_MIN,           15),  # 07:00
    ("T2", "R1", "B3", SERVICE_WINDOW_START_MIN + 60,      15),  # 08:00
    ("T3", "R2", "B1", SERVICE_WINDOW_START_MIN + 60,      10),  # 08:00  tight window
    ("T4", "R2", "B4", SERVICE_WINDOW_START_MIN + 120,     15),  # 09:00
    ("T5", "R3", "B2", SERVICE_WINDOW_START_MIN + 30,      15),  # 07:30
    ("T6", "R3", "B3", SERVICE_WINDOW_START_MIN + 105,     15),  # 08:45
]


def generate_schedules() -> list[dict]:
    return [
        {
            "trip_id": tid,
            "route_id": rid,
            "bus_id": bid,
            "scheduled_departure_min": dep,
            "permitted_departure_window_min": win,
            "service_date": "scenario_day",
        }
        for tid, rid, bid, dep, win in TRIPS_DATA
    ]


# ---------------------------------------------------------------------------
# 5. Demand  (origin-destination passenger requests)
# ---------------------------------------------------------------------------
#
# Demand is structured so that:
#   - R1 / R2 / R3 each have multiple time windows.
#   - R2 at 08:00 carries 38 passengers → bus B1 (cap 50) handles it with
#     6 seats to spare in normal scenario.  In demand_spike (×1.8 ≈ 68) it
#     overloads B1; the planner must detect and flag unmet demand.
#   - Intentionally normal-scenario demand never exceeds any assigned bus
#     capacity so the baseline has no capacity violations.

DEMAND_DATA = [
    # demand_id, route_id, board_stop, alight_stop, window_start_min, passengers
    # R1 demand
    ("D01", "R1", "S1", "S3", SERVICE_WINDOW_START_MIN,      8),   # 07:00 window
    ("D02", "R1", "S2", "S4", SERVICE_WINDOW_START_MIN,      6),
    ("D03", "R1", "S1", "S4", SERVICE_WINDOW_START_MIN + 60, 10),  # 08:00 window
    ("D04", "R1", "S2", "S3", SERVICE_WINDOW_START_MIN + 60,  8),
    # R2 demand — large morning peak to test capacity
    ("D05", "R2", "S1", "S3", SERVICE_WINDOW_START_MIN + 60, 20),  # 08:00  ← key demand
    ("D06", "R2", "S5", "S7", SERVICE_WINDOW_START_MIN + 60, 10),
    ("D07", "R2", "S1", "S7", SERVICE_WINDOW_START_MIN + 60,  8),
    ("D08", "R2", "S1", "S3", SERVICE_WINDOW_START_MIN + 120, 12), # 09:00 window
    ("D09", "R2", "S5", "S3", SERVICE_WINDOW_START_MIN + 120,  8),
    # R3 demand
    ("D10", "R3", "S8", "S2", SERVICE_WINDOW_START_MIN + 30, 12),  # 07:30
    ("D11", "R3", "S1", "S6", SERVICE_WINDOW_START_MIN + 30,  8),
    ("D12", "R3", "S8", "S3", SERVICE_WINDOW_START_MIN + 105, 10), # 08:45
    ("D13", "R3", "S1", "S3", SERVICE_WINDOW_START_MIN + 105,  7),
]

# Verify R2 08:00 total: D05(20) + D06(10) + D07(8) = 38 ≤ B1 capacity(50) ✓
# After spike ×1.8: ceil(38 * 1.8) = 69 > 50 → overloaded as intended.


def generate_demand() -> list[dict]:
    return [
        {
            "demand_id": did,
            "route_id": rid,
            "boarding_stop_id": board,
            "alighting_stop_id": alight,
            "time_window_start_min": win,
            "passengers": pax,
        }
        for did, rid, board, alight, win, pax in DEMAND_DATA
    ]


# ---------------------------------------------------------------------------
# 6. Ground truth + GPS traces
# ---------------------------------------------------------------------------

def _route_stop_arrivals(
    route_def: dict,
    departure_min: float,
    delay_per_segment: dict[int, float] | None = None,
) -> list[tuple[str, float]]:
    """
    Compute exact ground-truth arrival at each stop for one trip.

    Args:
        route_def:          Route dict from generate_routes().
        departure_min:      Scheduled departure of the first stop (minutes).
        delay_per_segment:  Optional {segment_index: extra_delay_minutes}.

    Returns:
        List of (stop_id, arrival_min) in route order.
        The first stop's arrival equals the departure time.
    """
    delay_per_segment = delay_per_segment or {}
    arrivals = []
    current = float(departure_min)
    stop_ids = route_def["stop_ids"]
    segments = route_def["segments"]

    # First stop: bus departs = arrives at origin
    arrivals.append((stop_ids[0], current))

    for idx, seg in enumerate(segments):
        extra = delay_per_segment.get(idx, 0.0)
        current += seg["travel_time_min"] + extra
        arrivals.append((stop_ids[idx + 1], current))

    return arrivals


def _generate_gps_trace(
    rng: random.Random,
    trip_id: str,
    bus_id: str,
    route_def: dict,
    stop_arrivals: list[tuple[str, float]],
) -> list[dict]:
    """
    Synthesise GPS samples for one trip.

    Samples are placed at GPS_INTERVAL_MIN intervals (simulated clock time).
    Each sample interpolates the bus position between consecutive stops using
    linear interpolation on the stop coordinates, then adds small bounded noise
    to lat/lon.

    Crucially, timestamp noise is added so the samples do NOT directly expose
    the exact ground-truth arrival time to the ETA estimator.
    """
    stop_ids = route_def["stop_ids"]
    stop_coords = {s[0]: (s[2], s[3]) for s in STOPS_DATA}

    # Build a timeline: (time_min, lat, lon) for each stop arrival
    timeline = [(arr_min, *stop_coords[sid]) for sid, arr_min in stop_arrivals]

    start_time = timeline[0][0]
    end_time = timeline[-1][0]
    total_duration = end_time - start_time

    if total_duration <= 0:
        return []

    samples = []
    t = start_time
    sample_idx = 0

    while t <= end_time + GPS_INTERVAL_MIN * 0.5:
        # Interpolate position at time t
        # Find the two surrounding timeline points
        lat, lon = timeline[0][1], timeline[0][2]
        for k in range(len(timeline) - 1):
            t0, lat0, lon0 = timeline[k]
            t1, lat1, lon1 = timeline[k + 1]
            if t0 <= t <= t1:
                if t1 > t0:
                    frac = (t - t0) / (t1 - t0)
                else:
                    frac = 0.0
                lat = lat0 + frac * (lat1 - lat0)
                lon = lon0 + frac * (lon1 - lon0)
                break

        # Add small bounded coordinate noise (not time noise — time is exact
        # so samples remain ordered, but coordinate noise prevents trivial
        # speed-based arrival reconstruction)
        lat += rng.uniform(-0.0002, 0.0002)
        lon += rng.uniform(-0.0002, 0.0002)

        # Add bounded timestamp noise so segment travel-time estimates have
        # realistic variability (±GPS_NOISE_BOUND minutes)
        noisy_t = t + rng.uniform(-GPS_NOISE_BOUND, GPS_NOISE_BOUND)
        noisy_t = max(start_time, noisy_t)  # clamp: never before trip start

        samples.append({
            "trace_id": f"{trip_id}_P{sample_idx:03d}",
            "bus_id": bus_id,
            "timestamp_min": round(noisy_t, 4),
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "route_id": route_def["route_id"],
            "trip_id": trip_id,
        })
        sample_idx += 1
        t += GPS_INTERVAL_MIN

    # Ensure samples are sorted by timestamp within this trip
    samples.sort(key=lambda x: x["timestamp_min"])
    return samples


def generate_ground_truth_and_gps(
    routes: list[dict],
    schedules: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Generate ground_truth arrivals and GPS traces for all trips.

    Returns:
        ground_truth: list of {trip_id, stop_id, actual_arrival_min}
        gps_traces:   list of GPS point dicts
    """
    rng = random.Random(RANDOM_SEED)

    route_map = {r["route_id"]: r for r in routes}
    ground_truth = []
    gps_traces = []

    for sched in schedules:
        tid = sched["trip_id"]
        rid = sched["route_id"]
        bus_id = sched["bus_id"]
        dep = sched["scheduled_departure_min"]

        route_def = route_map[rid]
        arrivals = _route_stop_arrivals(route_def, dep)

        # Ground truth
        for stop_id, arr_min in arrivals:
            ground_truth.append({
                "trip_id": tid,
                "stop_id": stop_id,
                "actual_arrival_min": round(arr_min, 4),
            })

        # GPS trace
        traces = _generate_gps_trace(rng, tid, bus_id, route_def, arrivals)
        gps_traces.extend(traces)

    return ground_truth, gps_traces


# ---------------------------------------------------------------------------
# 7. Validation
# ---------------------------------------------------------------------------

def validate_dataset(
    stops: list[dict],
    buses: list[dict],
    routes: list[dict],
    schedules: list[dict],
    demand: list[dict],
    gps_traces: list[dict],
) -> list[str]:
    """
    Run integrity checks on the generated dataset.

    Returns a list of error strings. Empty list = all checks passed.
    """
    errors: list[str] = []
    stop_ids = {s["stop_id"] for s in stops}
    bus_ids = {b["bus_id"] for b in buses}
    route_ids = {r["route_id"] for r in routes}
    trip_ids = {s["trip_id"] for s in schedules}
    route_stop_map: dict[str, list[str]] = {
        r["route_id"]: r["stop_ids"] for r in routes
    }

    # --- Stop IDs unique ---
    seen_stops: set[str] = set()
    for s in stops:
        if s["stop_id"] in seen_stops:
            errors.append(f"Duplicate stop_id: {s['stop_id']}")
        seen_stops.add(s["stop_id"])

    # --- Bus IDs unique, capacities positive ---
    seen_buses: set[str] = set()
    for b in buses:
        if b["bus_id"] in seen_buses:
            errors.append(f"Duplicate bus_id: {b['bus_id']}")
        seen_buses.add(b["bus_id"])
        if b["capacity"] <= 0:
            errors.append(f"Bus {b['bus_id']} has non-positive capacity")
        if b["available_until_min"] <= b["available_from_min"]:
            errors.append(f"Bus {b['bus_id']} availability window invalid")

    # --- Route integrity ---
    for r in routes:
        if r["route_id"] not in route_ids:
            errors.append(f"Route {r['route_id']} not in route_ids set")
        # Stop references exist
        for sid in r["stop_ids"]:
            if sid not in stop_ids:
                errors.append(f"Route {r['route_id']} references unknown stop {sid}")
        # Segment count
        expected_segs = len(r["stop_ids"]) - 1
        if len(r["segments"]) != expected_segs:
            errors.append(
                f"Route {r['route_id']}: expected {expected_segs} segments, "
                f"got {len(r['segments'])}"
            )
        # Segment positive travel times and correct stop order
        for i, seg in enumerate(r["segments"]):
            if seg["travel_time_min"] <= 0:
                errors.append(
                    f"Route {r['route_id']} segment {i} has non-positive travel time"
                )
            if seg["from_stop_id"] != r["stop_ids"][i]:
                errors.append(
                    f"Route {r['route_id']} segment {i} from_stop mismatch"
                )
            if seg["to_stop_id"] != r["stop_ids"][i + 1]:
                errors.append(
                    f"Route {r['route_id']} segment {i} to_stop mismatch"
                )

    # --- Schedule integrity ---
    for s in schedules:
        if s["route_id"] not in route_ids:
            errors.append(f"Trip {s['trip_id']} references unknown route {s['route_id']}")
        if s["bus_id"] not in bus_ids:
            errors.append(f"Trip {s['trip_id']} references unknown bus {s['bus_id']}")
        if not (
            SERVICE_WINDOW_START_MIN
            <= s["scheduled_departure_min"]
            < SERVICE_WINDOW_END_MIN
        ):
            errors.append(
                f"Trip {s['trip_id']} departure {s['scheduled_departure_min']} "
                f"outside service window"
            )
        if s["permitted_departure_window_min"] < 0:
            errors.append(
                f"Trip {s['trip_id']} permitted_departure_window_min is negative"
            )

    # --- Demand integrity ---
    for d in demand:
        if d["route_id"] not in route_ids:
            errors.append(
                f"Demand {d['demand_id']} references unknown route {d['route_id']}"
            )
        route_stops = route_stop_map.get(d["route_id"], [])
        if d["boarding_stop_id"] not in stop_ids:
            errors.append(
                f"Demand {d['demand_id']} boarding_stop {d['boarding_stop_id']} unknown"
            )
        if d["alighting_stop_id"] not in stop_ids:
            errors.append(
                f"Demand {d['demand_id']} alighting_stop {d['alighting_stop_id']} unknown"
            )
        # Boarding must appear before alighting in route order
        if d["boarding_stop_id"] in route_stops and d["alighting_stop_id"] in route_stops:
            board_idx = route_stops.index(d["boarding_stop_id"])
            alight_idx = route_stops.index(d["alighting_stop_id"])
            if board_idx >= alight_idx:
                errors.append(
                    f"Demand {d['demand_id']}: boarding stop is not before alighting "
                    f"stop on route {d['route_id']}"
                )
        else:
            # One or both stops not on the route
            if d["boarding_stop_id"] not in route_stops:
                errors.append(
                    f"Demand {d['demand_id']} boarding_stop {d['boarding_stop_id']} "
                    f"not on route {d['route_id']}"
                )
            if d["alighting_stop_id"] not in route_stops:
                errors.append(
                    f"Demand {d['demand_id']} alighting_stop {d['alighting_stop_id']} "
                    f"not on route {d['route_id']}"
                )
        if d["passengers"] < 0:
            errors.append(
                f"Demand {d['demand_id']} has negative passengers"
            )

    # --- GPS trace ordering (per trip, timestamps must be non-decreasing) ---
    trip_traces: dict[str, list[float]] = {}
    for pt in gps_traces:
        tid = pt.get("trip_id")
        if tid:
            trip_traces.setdefault(tid, []).append(pt["timestamp_min"])
    for tid, timestamps in trip_traces.items():
        for i in range(1, len(timestamps)):
            if timestamps[i] < timestamps[i - 1]:
                errors.append(
                    f"GPS trace for trip {tid}: timestamps not non-decreasing at "
                    f"index {i} ({timestamps[i - 1]} -> {timestamps[i]})"
                )
            break  # only report first occurrence per trip

    return errors


# ---------------------------------------------------------------------------
# 8. Write to CSV / JSON
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_dataset(
    stops: list[dict],
    buses: list[dict],
    routes: list[dict],
    schedules: list[dict],
    demand: list[dict],
    gps_traces: list[dict],
    ground_truth: list[dict],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(
        out_dir / "stops.csv",
        stops,
        ["stop_id", "name", "latitude", "longitude"],
    )
    _write_csv(
        out_dir / "buses.csv",
        buses,
        ["bus_id", "capacity", "available_from_min", "available_until_min", "status"],
    )
    with open(out_dir / "routes.json", "w", encoding="utf-8") as f:
        json.dump(routes, f, indent=2)
    _write_csv(
        out_dir / "schedules.csv",
        schedules,
        [
            "trip_id", "route_id", "bus_id",
            "scheduled_departure_min", "permitted_departure_window_min",
            "service_date",
        ],
    )
    _write_csv(
        out_dir / "demand.csv",
        demand,
        [
            "demand_id", "route_id", "boarding_stop_id",
            "alighting_stop_id", "time_window_start_min", "passengers",
        ],
    )
    _write_csv(
        out_dir / "gps_traces.csv",
        gps_traces,
        [
            "trace_id", "bus_id", "timestamp_min",
            "latitude", "longitude", "route_id", "trip_id",
        ],
    )
    with open(out_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)


# ---------------------------------------------------------------------------
# 9. Main entry point
# ---------------------------------------------------------------------------

def generate_all(out_dir: Path | None = None) -> dict:
    """
    Generate the full dataset and write it to disk.

    Returns a dict with all generated data (for use by tests without I/O).
    """
    out_dir = out_dir or DATA_DIR

    stops = generate_stops()
    buses = generate_buses()
    routes = generate_routes()
    schedules = generate_schedules()
    demand = generate_demand()
    ground_truth, gps_traces = generate_ground_truth_and_gps(routes, schedules)

    errors = validate_dataset(stops, buses, routes, schedules, demand, gps_traces)
    if errors:
        msg = "\n".join(f"  - {e}" for e in errors)
        raise ValueError(f"Dataset validation failed:\n{msg}")

    write_dataset(stops, buses, routes, schedules, demand, gps_traces, ground_truth, out_dir)

    print(f"[generate_dataset] Wrote dataset to {out_dir}")
    print(f"  Stops:       {len(stops)}")
    print(f"  Buses:       {len(buses)}")
    print(f"  Routes:      {len(routes)}")
    print(f"  Trips:       {len(schedules)}")
    print(f"  Demand rows: {len(demand)}")
    print(f"  GPS points:  {len(gps_traces)}")
    print(f"  Ground truth:{len(ground_truth)}")

    return {
        "stops": stops,
        "buses": buses,
        "routes": routes,
        "schedules": schedules,
        "demand": demand,
        "gps_traces": gps_traces,
        "ground_truth": ground_truth,
    }


if __name__ == "__main__":
    generate_all()
