"""
RoutePulse server smoke test.

Starts uvicorn as a subprocess, waits for it to be ready, exercises every
API endpoint once, and prints a pass/fail summary.  Exits 0 on success.

Usage:
    cd backend
    python smoke_test.py
"""

import subprocess
import sys
import time
import pathlib
import json

try:
    import urllib.request as req
    import urllib.error as uerr
except ImportError:
    sys.exit("urllib not available")

BASE = "http://localhost:8001"   # port 8001 avoids clash with a running dev server
TIMEOUT = 20                     # seconds to wait for startup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get(path: str) -> tuple[int, dict]:
    try:
        with req.urlopen(f"{BASE}{path}", timeout=10) as r:
            return r.status, json.loads(r.read())
    except uerr.HTTPError as e:
        return e.code, json.loads(e.read())


def post(path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    r_obj = req.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with req.urlopen(r_obj, timeout=30) as r:
            return r.status, json.loads(r.read())
    except uerr.HTTPError as e:
        return e.code, json.loads(e.read())


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = "✓" if ok else "✗"
    print(f"  {icon}  {name}" + (f"  [{detail}]" if detail else ""))


# ---------------------------------------------------------------------------
# Start server
# ---------------------------------------------------------------------------

print("\n── Starting uvicorn on port 8001 ──")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8001", "--log-level", "warning"],
    cwd=str(pathlib.Path(__file__).parent),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Wait for /api/health to respond
deadline = time.time() + TIMEOUT
ready = False
while time.time() < deadline:
    try:
        code, body = get("/api/health")
        if code == 200 and body.get("status") == "ok":
            ready = True
            break
    except Exception:
        pass
    time.sleep(0.4)

if not ready:
    stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    proc.terminate()
    print(f"\n✗  Server did not start within {TIMEOUT}s.\n{stderr_out}")
    sys.exit(1)

print("  Server ready.\n")

# ---------------------------------------------------------------------------
# Smoke checks
# ---------------------------------------------------------------------------

print("── Endpoint checks ──")

# 1. Health
code, body = get("/api/health")
check("GET /api/health → 200 {status:ok}",
      code == 200 and body.get("status") == "ok",
      f"status={code}")

# 2. Network
code, body = get("/api/network")
check("GET /api/network → 200",              code == 200,                   f"status={code}")
check("  buses present",                     len(body.get("buses", [])) == 4)
check("  stops present",                     len(body.get("stops", [])) >= 8)
check("  routes present",                    len(body.get("routes", [])) == 3)
check("  trips present",                     len(body.get("trips", [])) >= 6)
check("  dataset_seed == 42",               body.get("dataset_seed") == 42,
      f"got {body.get('dataset_seed')}")

# 3. Scenarios list
code, body = get("/api/scenarios")
check("GET /api/scenarios → 200",            code == 200,                   f"status={code}")
ids = {s["scenario_id"] for s in body} if isinstance(body, list) else set()
check("  all 4 scenario IDs present",
      ids == {"normal", "demand_spike", "bus_delay", "vehicle_unavailable"},
      f"got {ids}")

# 4. Baseline — all four scenarios
for sid in ("normal", "demand_spike", "bus_delay", "vehicle_unavailable"):
    code, body = get(f"/api/scenarios/{sid}/baseline")
    check(f"GET /api/scenarios/{sid}/baseline → 200",
          code == 200, f"status={code}")
    if code == 200:
        check(f"  alerts is list",    isinstance(body.get("alerts"), list))
        check(f"  eta_predictions populated",
              len(body.get("eta_predictions", {})) > 0)

# 5. Baseline — unknown scenario → 404
code, body = get("/api/scenarios/does_not_exist/baseline")
check("GET /api/scenarios/unknown/baseline → 404",
      code == 404, f"status={code}")
check("  detail contains bad ID",
      "does_not_exist" in body.get("detail", ""),
      repr(body.get("detail", "")))
check("  no Traceback in response",
      "Traceback" not in json.dumps(body))

# 6. POST /api/plan — all four scenarios
for sid in ("normal", "demand_spike", "bus_delay", "vehicle_unavailable"):
    code, body = post("/api/plan", {"scenario_id": sid})
    check(f"POST /api/plan {sid} → 200",
          code == 200, f"status={code}")
    if code == 200:
        status = body.get("recommendation_status", "")
        check(f"  recommendation_status valid",
              status in ("improvement_found", "no_improvement", "infeasible_baseline"),
              status)
        bm = body.get("baseline_metrics", {})
        cost = bm.get("objective_cost")
        check(f"  objective_cost is finite number",
              isinstance(cost, (int, float)) and cost == cost,   # NaN check
              str(cost))
        cv = body.get("constraint_validation", {})
        if body.get("proposed_metrics") is not None:
            check(f"  proposed plan passed constraints",
                  cv.get("passed") is True,
                  f"passed={cv.get('passed')}, violations={len(cv.get('violations', []))}")

# 7. POST /api/plan — unknown scenario → 404
code, body = post("/api/plan", {"scenario_id": "unknown_xyz"})
check("POST /api/plan unknown → 404",
      code == 404, f"status={code}")
check("  no stack trace",
      "Traceback" not in json.dumps(body))

# 8. POST /api/plan — malformed body → 422
code, body = post("/api/plan", {})
check("POST /api/plan missing scenario_id → 422",
      code == 422, f"status={code}")

# 9. Comparison endpoint
for sid in ("normal", "demand_spike"):
    code_get, body_get = get(f"/api/scenarios/{sid}/comparison")
    code_post, body_post = post("/api/plan", {"scenario_id": sid})
    check(f"GET /api/scenarios/{sid}/comparison → 200",
          code_get == 200, f"status={code_get}")
    if code_get == 200 and code_post == 200:
        cost_get  = body_get.get("baseline_metrics", {}).get("objective_cost")
        cost_post = body_post.get("baseline_metrics", {}).get("objective_cost")
        check(f"  comparison cost == POST plan cost for {sid}",
              cost_get == cost_post,
              f"GET={cost_get} POST={cost_post}")

# 10. Objective weight override changes cost for demand_spike
code1, r1 = post("/api/plan", {"scenario_id": "demand_spike"})
code2, r2 = post("/api/plan", {
    "scenario_id": "demand_spike",
    "objective_weights": {"waiting_time": 1.0, "unmet_passengers": 100.0, "missed_connections": 10.0}
})
if code1 == 200 and code2 == 200:
    unmet = r1.get("baseline_metrics", {}).get("unmet_passengers", 0)
    c1 = r1.get("baseline_metrics", {}).get("objective_cost", 0)
    c2 = r2.get("baseline_metrics", {}).get("objective_cost", 0)
    check("Objective weight override changes cost (demand_spike)",
          unmet == 0 or c2 > c1,
          f"unmet={unmet} default_cost={c1} heavy_cost={c2}")

# ---------------------------------------------------------------------------
# Shut down and summarise
# ---------------------------------------------------------------------------

proc.terminate()
proc.wait(timeout=5)

print()
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total  = len(results)

print(f"── Summary: {passed}/{total} checks passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    print("\nFailed checks:")
    for name, ok, detail in results:
        if not ok:
            print(f"  ✗  {name}  [{detail}]")
else:
    print(" ✓")

print()
sys.exit(0 if failed == 0 else 1)
