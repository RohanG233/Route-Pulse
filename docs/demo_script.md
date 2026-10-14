# RoutePulse Demo Script (2–3 minutes)

This script walks an operator or judge through the full planning workflow.
All metric values shown are computed from the synthetic dataset — they are
not hardcoded and will match what appears on screen.

---

## Before the demo — 30-second setup

```bash
# Terminal 1
cd backend
python -m app.data.generate_dataset   # reset to clean state
uvicorn app.main:app --reload --port 8000

# Terminal 2
cd frontend
npm run dev          # or: node ".../npm-cli.js" run dev on Windows
```

Open `http://localhost:5173`. Confirm the header shows **API ✓ online**.

---

## Step 1 — Introduce RoutePulse (15 sec)

> "RoutePulse is a campus transport **planning** tool, not just a
> vehicle-location map. It detects capacity and schedule problems,
> finds feasible operational changes, and shows measurable before-and-after
> results — all grounded in constraint validation."

Point to the **Network Overview** cards:
- 4 buses (50, 40, 30, 20 seats) — availability 07:00–10:00
- 3 routes, 8 stops, 6 scheduled trips
- Dataset seed 42 — same inputs every run

---

## Step 2 — Normal scenario (20 sec)

Select **Normal**.

- The **ETA table** fills in — arrivals computed from GPS traces, not hardcoded.
  Note the "GPS" / "fallback" source badge on each stop.
- The **Alerts** panel shows "✓ No alerts" — the baseline schedule is clean.
- The **Route & Schedule table** shows all trips within capacity.

> "In the normal scenario the baseline has no hard-constraint violations.
> This is our reference point."

---

## Step 3 — Demand Spike (50 sec)

Select **Demand Spike**.

The alert panel immediately shows:

| Code | Severity | Message |
|------|----------|---------|
| UNMET_DEMAND | warning | Trip T3: N passengers could not board (capacity 50, max load 50) |

Point to the Route table — T3 shows **50/50 (100%)** load, unmet passengers
listed.

Click **Run Planning / Optimise** (wait ~1 second).

The **Recommendation panel** appears:

- **Status badge**: either `✓ Improvement found` or `– No improvement found`
  (depends on whether a feasible reassignment exists within the search space).
- **Constraints**: ✓ All passed (C1–C7) — the proposed plan was validated.
- **Action**: e.g. "Reassign trip T3 from bus B1 (50 seats) to bus B2 (40 seats)
  to reduce unmet demand…" — with the plain-language reason.

The **Comparison panel** shows:

| Metric | Baseline | Proposed | Change |
|--------|----------|----------|--------|
| Unmet passengers | ≥ 19 | ≤ baseline | shown |
| Objective cost | computed | lower | +N% |
| Fleet utilisation | computed | computed | shown |
| ETA MAE | computed min | — | — |

> "The improvement percentage is calculated from actual metric values —
> never a hardcoded number."

---

## Step 4 — Vehicle Unavailable (25 sec)

Select **Vehicle Unavailable**. Click **Run Planning**.

- **Status**: `⚠ Baseline infeasible` — Bus B1 is assigned to T3 but is
  marked unavailable. This is a C2 hard-constraint violation.
- The planner searches for a feasible alternative. If one is found it shows
  the reassignment and confirms all constraints pass.
- If no feasible alternative exists the system reports clearly:
  *"Manual intervention required"* — it never fabricates a result.

> "The constraint validator prevents the planner from ever accepting a plan
> that assigns an unavailable bus. Constraint checks are non-negotiable."

---

## Step 5 — Bus Delay (15 sec)

Select **Bus Delay**.

- The **ETA table** shows T3's arrivals shifted ~10 minutes later than normal.
- **LATE_ARRIVAL** alerts appear for affected stops on T3.
- Running the planner evaluates whether a departure shift can mitigate the
  delay within T3's permitted window (±10 min).

---

## Step 6 — Honest limitations (10 sec)

> "A few things to be clear about:
> - All data is synthetic — illustrative campus coordinates, not a real fleet.
> - GPS traces are simulated; ETA accuracy is measured against simulation
>   ground truth, not real-world arrivals.
> - Routes are fixed sequences — no dynamic re-routing.
> - The planner makes one-trip changes at a time."

Point to the **Assumptions & limitations** dropdown in the recommendation panel.

---

## Reset at any time

```bash
cd backend
python -m app.data.generate_dataset   # overwrites files — same seed, same results
```

Refresh the browser. The dashboard returns to the initial state.

---

## Key design points for judges

| Claim | Evidence |
|-------|---------|
| Metrics are computed, not hardcoded | `demand_spike` and `normal` baselines return different `objective_cost` |
| All accepted plans pass C1–C7 | `constraint_validation.passed == true` in every PlanResponse |
| Improvement % is real | `proposed_metrics.objective_cost < baseline_metrics.objective_cost` enforced in tests |
| Same demand for baseline and proposed | Both plans use identical scenario-transformed inputs |
| Deterministic | Re-running any scenario produces byte-identical results |
| Transparent infeasibility | `vehicle_unavailable` shows C2 violation details, never silently passes |
