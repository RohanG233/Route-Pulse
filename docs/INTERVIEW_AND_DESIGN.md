# RoutePulse — Interview, Hackathon & System Design Reference

Everything you need to explain this project confidently: what it is, why you
built it the way you did, how every piece works, and the answers to every
question you are likely to be asked.

---

## Table of Contents

1. [30-Second Elevator Pitch](#1-30-second-elevator-pitch)
2. [Problem Statement](#2-problem-statement)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Data Model — Every Entity Explained](#4-data-model)
5. [The Synthetic Dataset — Design Decisions](#5-the-synthetic-dataset)
6. [ETA Estimation — How It Works](#6-eta-estimation)
7. [Demand & Load Simulation — How It Works](#7-demand--load-simulation)
8. [Hard Constraint Validator — C1 to C7](#8-hard-constraint-validator)
9. [Planning Engine — The Search Algorithm](#9-planning-engine)
10. [Metrics & Objective Function](#10-metrics--objective-function)
11. [Scenarios — What Each One Tests](#11-scenarios)
12. [API Design Decisions](#12-api-design-decisions)
13. [Frontend Architecture](#13-frontend-architecture)
14. [Testing Strategy — 265 Tests](#14-testing-strategy)
15. [Deployment Readiness](#15-deployment-readiness)
16. [Technology Choices — Why Each One](#16-technology-choices)
17. [Limitations You Should Proactively Mention](#17-limitations)
18. [Interview Questions & Model Answers](#18-interview-questions--model-answers)
19. [Hackathon Judging Questions & Model Answers](#19-hackathon-judging-questions--model-answers)
20. [Key Numbers to Remember](#20-key-numbers-to-remember)

---

## 1. 30-Second Elevator Pitch

> "RoutePulse is a campus transport planning tool for operators. It is not a
> vehicle-location map — the value is an **explainable, constraint-aware
> planning recommendation with measurable outcomes**.
>
> You select a scenario — normal operations, demand spike, bus delay, or a bus
> going offline — and the system detects problems, searches for a feasible
> fix, validates it against seven hard constraints, and shows you the
> before-and-after comparison. Every metric you see was computed from the
> data, not hardcoded."

---

## 2. Problem Statement

Campus transport operators face three recurring challenges:

| Challenge | What RoutePulse does about it |
|-----------|------------------------------|
| **Demand exceeds capacity** on busy routes at peak hours | Detects unmet passengers and recommends a bus reallocation if one is feasible |
| **Disruptions** (delays, breakdowns) cascade into missed connections and late arrivals | Updates ETA windows and re-evaluates the plan under the disrupted conditions |
| **"Is this change actually better?"** — hard to measure without a tool | Computes before/after metrics (waiting time, unmet demand, fleet utilisation) from the same data so comparisons are honest |

The project deliberately avoided the common failure mode of a demo that shows
pretty numbers that were hardcoded. Every metric flows from an actual computation.

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                              │
│  React + TypeScript + Vite (port 5173)                      │
│  ScenarioSelector → RouteTable → EtaTable → AlertList       │
│  → Run Planning → RecommendationPanel → ComparisonPanel     │
└───────────────┬─────────────────────────────────────────────┘
                │  HTTP (CORS configured; VITE_API_URL)
                ▼
┌─────────────────────────────────────────────────────────────┐
│                FastAPI Backend (port 8000)                  │
│                                                             │
│  ┌──────────┐   ┌───────────┐   ┌──────────────────────┐  │
│  │  /health │   │ /network  │   │        /plan          │  │
│  │          │   │ /scenarios│   │   POST → full cycle   │  │
│  │          │   │ /baseline │   │   GET → comparison    │  │
│  └──────────┘   └───────────┘   └──────────────────────┘  │
│                                                             │
│  app.state.dataset (loaded once at startup, read-only)     │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  eta_service → demand_service → constraint_validator │   │
│  │  planner_service → metrics_service → alert_service   │   │
│  └─────────────────────────────────────────────────────┘   │
└───────────────┬─────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────┐
│              Generated Dataset (app/data/generated/)        │
│  stops.csv  buses.csv  routes.json  schedules.csv           │
│  demand.csv  gps_traces.csv  ground_truth.json              │
│                                                             │
│  Seed: 42 — same seed always produces identical files       │
└─────────────────────────────────────────────────────────────┘
```

**Key architectural choice: request-time computation, not cached results.**
Every call to `/api/plan` runs the full pipeline from scratch. This means
the comparison between baseline and proposed is always done under the
identical scenario inputs — the spec explicitly required this to prevent
giving the proposed plan "easier" test conditions.

---

## 4. Data Model

### Entities and their relationships

```
Bus ────────────────── serves ─────────────────── Trip
 │                                                  │
 │ bus_id, capacity,                    trip_id, route_id,
 │ available_from_min,                  bus_id, scheduled_departure_min,
 │ available_until_min, status          permitted_departure_window_min
 │                                                  │
 │                                                  ▼
Stop ──── ordered in ──── Route ────── is run by ──Trip
 │                          │
 │ stop_id, name,          route_id, name,
 │ latitude, longitude     stop_ids (ordered list),
                           segments (travel_time_min per pair)
                                │
                                ▼
                           Demand
                           demand_id, route_id,
                           boarding_stop_id, alighting_stop_id,
                           time_window_start_min, passengers

                           GpsTrace → per trip, timestamped lat/lon
                           GroundTruth → actual simulated arrival (eval only)
```

**All times are in minutes from midnight.**
- `420` = 07:00, `480` = 08:00, `540` = 09:00, `600` = 10:00
- This avoids time-zone complexity. HH:MM strings are only created at the API boundary.

### Why ordered stop IDs instead of a graph?

Routes are fixed sequences — no dynamic rerouting. Representing them as
`stop_ids: list[str]` makes the order explicit and means every operation
(ETA accumulation, demand boarding/alighting, constraint checking) iterates
in the same known order. There is no ambiguity.

---

## 5. The Synthetic Dataset

### Why synthetic data?

No real campus fleet data was available, and the spec required a dataset
realistic enough to exercise the planning logic, not one so simple that
constraints are never triggered.

### The intentional design decisions

The dataset was engineered so that:

| Design choice | What scenario it enables |
|--------------|--------------------------|
| R2 at 08:00 carries exactly **38 passengers** (D05=20 + D06=10 + D07=8), which fits in B1's 50 seats with 12 to spare | Normal scenario is clean |
| After ×1.8 spike: ceil(20×1.8)+ceil(10×1.8)+ceil(8×1.8) = 36+18+15 = **69 passengers > 50 capacity** | demand_spike creates unmet demand |
| T3 (R2, 08:00) has a **tight departure window of ±10 min** | Limits planner options on that trip |
| B1 (50 seats) is the only bus assigned to R2 at 08:00 in the baseline | vehicle_unavailable creates an infeasible baseline |
| B2 serves T1 (ends 07:12) then T5 (starts 07:30) — **18-min gap** ≥ required 10-min buffer | Normal baseline has no C4 violations |

### Reproducibility

Seed `42` is passed to Python's `random.Random(42)`. The segment travel
times are **not randomised** — they are fixed constants. Only GPS coordinate
noise (±0.0002°) and timestamp noise (±0.3 min) are seeded. Running
`generate_all()` twice with the same seed produces byte-identical CSV/JSON.

### The complete schedule

```
Trip  Route  Bus   Departs  Window  Ends    Capacity
T1    R1     B2    07:00    ±15min  07:12   40 seats
T2    R1     B3    08:00    ±15min  08:12   30 seats
T3    R2     B1    08:00    ±10min  08:16   50 seats  ← key trip
T4    R2     B4    09:00    ±15min  09:16   20 seats
T5    R3     B2    07:30    ±15min  07:51   40 seats
T6    R3     B3    08:45    ±15min  09:06   30 seats
```

Route travel times: R1 = 4+4+4 = 12 min | R2 = 3+4+5+4 = 16 min | R3 = 5+4+3+4+5 = 21 min

---

## 6. ETA Estimation

### The algorithm (no ground truth used)

```
For each route segment:
  1. Collect GPS observations across ALL trips on that route.
  2. For each trip, assign GPS points to stops proportionally by index:
       stop_timestamp[i] = GPS_point at index round(i/(n_stops-1) * total_pts)
       segment_time[s]   = stop_timestamp[s+1] - stop_timestamp[s]
       (discard if ≤ 0)
  3. If ≥ 3 observations: use median (robust to outliers)
     Else: fall back to configured segment travel time
  4. Add scenario delay if this trip is affected (e.g. bus_delay adds 10 min)
  5. Accumulate: ETA[stop] = departure + sum(estimated_times to that stop)
  6. Window: [ETA - half_range, ETA + half_range]
     where half_range = max(2.0, (max_sample - min_sample) / 2)
     and window_start is clamped to previous stop's ETA
```

### Why median, not mean?

Median is robust to outliers. If one GPS trace had an anomalously long
segment time (GPS dropout, re-connection), median ignores it. Mean would
inflate the estimate.

### Why proportional index bucketing instead of nearest-point-to-stop?

The stop coordinates are known, but mapping GPS lat/lon to the nearest stop
requires distance calculations and assumes the GPS point closest to the stop
coordinate is the one where the bus was at that stop. The proportional
bucketing approach is simpler, deterministic, and works correctly when GPS
samples are evenly distributed over the trip.

### The ground-truth isolation guarantee

`eta_service.py` never imports `load_ground_truth()`. This is enforced by
source-scan tests (`TestGroundTruthIsolation`). Ground truth is only
accessible from `metrics_service.py` for evaluation purposes. The distinction
matters because a real system would not have ground truth available at
prediction time.

---

## 7. Demand & Load Simulation

### The boarding loop

```python
For each stop in route order:
  1. Alight: remove all onboard groups whose alighting_stop == current_stop
  2. Board: for each demand group at this stop (in demand_id lex order):
       seats_left = capacity - sum(currently_onboard)
       if pax <= seats_left: board all → total_served += pax
       else: board seats_left, mark rest as unmet_demand
  3. Record StopLoadEvent: boarded, alighted, onboard_after, capacity,
     overloaded (onboard > capacity), unmet_at_stop
```

### Critical design choice: `seats_left` recomputed after each boarding group

The bug that was caught in testing: if you compute `seats_left` once per
stop and subtract a running `boarded` counter, you double-count already-seated
passengers. The fix is `seats_left = capacity - sum(c for _, c in onboard)`
where `onboard` is updated as each group boards.

### Demand matching: exact equality, not ≤

`time_window_start_min == trip.departure_min` (not `<=`). This ensures each
demand record is matched to exactly one trip, preventing double-counting
across multiple trips on the same route.

### Why lexicographic boarding order?

It is explicit, deterministic, and reproducible regardless of how Python
orders dictionaries. Any deterministic rule works for the MVP — the spec
required that the rule be documented.

---

## 8. Hard Constraint Validator

Every candidate plan passes through all seven constraints. **Any single
violation rejects the entire candidate.** There is no trade-off between
constraints and the objective function.

| Code | Rule | How Detected |
|------|------|-------------|
| **C1** | Onboard ≤ capacity at every stop | Checks `StopLoadEvent.overloaded` flag in demand results |
| **C2** | Trip fits within bus availability window; bus not unavailable | Compares trip start/end (from segment times + delays) against `available_from/until_min`; checks `bus.status == "unavailable"` |
| **C3** | No overlapping trips on the same bus | Sorts trips by start time; checks `start_B < end_A` for all pairs on the same bus |
| **C4** | ≥ 10-min repositioning gap between consecutive trips | Checks only adjacent pairs: `gap = start_next - end_prev`; fires if `gap < 10` |
| **C5** | Departure shift within permitted window | `abs(actual_departure - scheduled_departure) ≤ permitted_departure_window_min` |
| **C6** | Valid route stop order; positive segment times; arrival > departure | Checks `segment.travel_time_min > 0`; checks `last_arrival > first_departure` |
| **C7** | All referenced buses/routes/stops exist; demand OD order valid | Set lookups; checks boarding stop index < alighting stop index in route |

### Why separate C3 and C4?

A candidate can pass C3 (no overlap) but fail C4 (insufficient gap). For
example: Trip A ends at 08:12, Trip B starts at 08:18. That is a 6-minute
gap — no overlap, but below the 10-minute repositioning buffer. Without C4,
the planner might assign back-to-back trips that are physically impossible
to execute.

### The baseline can be infeasible

In the `vehicle_unavailable` scenario, T3 is assigned B1 which is marked
unavailable. The baseline itself fails C2. The system correctly reports
`infeasible_baseline`, runs the search anyway, and if it finds a valid
alternative it reports that too. It never silently accepts a plan with
violations.

---

## 9. Planning Engine

### The search strategy

```
1. Evaluate the baseline plan (metrics + constraint validation).
2. Generate all single-trip-change candidates:
   - For each of 6 trips:
     a. Bus reassignment: try each available bus ≠ current bus (3 options)
     b. Departure shift: try -15, -10, -5, +5, +10, +15 min (6 options)
   - Total: 6 × (3 + 6) = 54 candidates
3. For each candidate:
   a. Run full demand simulation (same scenario demand)
   b. Validate against C1–C7 — reject if any violation
   c. Compute objective cost
4. Select the feasible candidate with the lowest cost.
   Tie-break: lexicographic sort_key (trip_id:change_type:value).
5. If cost ≤ baseline cost: report no_improvement.
   If baseline was infeasible: report infeasible_baseline.
```

### Why only single-trip changes?

The spec limits the MVP to one trip changed at a time. This keeps the search
space small (54 candidates), deterministic, and explainable. Multi-trip
joint optimisation would require combinatorial search (54² = 2,916 pairs
minimum) with much harder explanation.

### Why 5-minute steps for departure shifts?

Coarser than 1-minute steps but fine enough to find meaningful improvements.
The dataset's permitted window is ±10 or ±15 minutes. With 5-minute steps,
that gives 3–6 meaningful shift options per trip.

### Determinism guarantee

1. Candidates are generated in a fixed order (sorted by `(trip_id, sort_key)`).
2. Constraint validation is deterministic (no randomness).
3. Demand simulation uses lexicographic boarding order.
4. Tie-breaking uses `sort_key` string comparison.

Running the planner twice on the same scenario always produces the same
recommendation.

### The objective function

```
cost = 1.0 × total_waiting_minutes
     + 5.0 × unmet_passengers
     + 10.0 × missed_connections
```

Weights are configurable via environment variable or per-request override.
The unmet_passengers weight (5×) is intentionally higher than waiting time
(1×) because a missed ride is worse than a short delay.
Missed connections are weighted highest (10×) but not modelled in the MVP —
reported as `"not_modelled"`.

**Verified live:** In `demand_spike`, unmet=36, cost = `5.0 × 36 = 180.0`.
With `unmet_passengers=100.0` override: cost = `100.0 × 36 = 3600.0`.

---

## 10. Metrics & Objective Function

### Waiting time formula

```
wait_per_demand_record = max(0, trip.departure_min - demand.time_window_start_min)
```

In the current dataset, all `time_window_start_min` exactly equal the
scheduled departure, so `wait = 0` in normal scenario. This is intentional —
waiting time becomes non-zero if the planner shifts a departure later than
the demand window.

The formula is applied proportionally:
```
total_wait += wait × passengers × (served_on_this_trip / total_demand_here)
avg_waiting_time = total_wait / total_served_passengers
```

### Fleet utilisation formula

```
utilisation_pct = (Σ trip_service_minutes) / (Σ bus_available_minutes) × 100

trip_service_minutes  = last_stop_arrival - departure (per assigned trip)
bus_available_minutes = available_until_min - available_from_min
                       = 600 - 420 = 180 min per bus
```

Unavailable buses contribute 0 to both sides.

### ETA quality (evaluated against ground truth — only in metrics, never in estimator)

```
MAE = mean(|predicted_arrival - actual_arrival|)
Window coverage = % of ground-truth arrivals inside [window_start, window_end]
```

Denominator = only pairs that exist in BOTH predictions AND ground truth.

### Why not just report one "score"?

The spec explicitly requires individual metrics to be shown separately so
a good score on one metric cannot mask a regression on another. You can see:
unmet passengers went from 36 to 0, but waiting time went up by 2 minutes.

---

## 11. Scenarios

### How transformations work

```
Scenario definition:
  - demand_overrides: {route_id: multiplier}  (applied with math.ceil)
  - segment_delay_overrides: {trip_id: extra_minutes}
  - unavailable_bus_ids: [bus_id, ...]

build_scenario_dataset() applies these to the base dataset,
returns typed Pydantic objects.

Both baseline AND proposed plans use the SAME transformed dataset.
```

### Scenario table with exact parameters

| Scenario | Transformation | Expected result |
|----------|---------------|-----------------|
| `normal` | None | Clean baseline, `no_improvement` |
| `demand_spike` | R2 demand × 1.8 (ceil per row): 20→36, 10→18, 8→15 = 69 total > 50 capacity | `improvement_found` (or `no_improvement` if no feasible bus available) |
| `bus_delay` | T3 all segments +10 min → T3 arrives ~10 min late at each stop | `no_improvement` (ETA windows shift, no unmet demand created) |
| `vehicle_unavailable` | B1 status = "unavailable" | `infeasible_baseline` (T3 assigned B1 → C2 violation) |

---

## 12. API Design Decisions

### `/api/plan` vs `/api/scenarios/{id}/comparison`

Both return the same `PlanResponse`. The GET comparison endpoint re-runs
the planner deterministically with default weights. This means the frontend
comparison panel can reload without requiring a prior POST. No hidden server
state is required — consistent with the spec's requirement for reproducibility.

### Why compute on every request instead of caching?

1. The dataset is small (6 trips, 54 candidates); the full planning cycle
   runs in under 2 seconds.
2. Caching would require cache invalidation logic and hidden state.
3. The spec explicitly says "do not depend on hidden mutable server state".

### Error codes are structured, never stack traces

```
Unknown scenario ID → 404 with {"detail": "Unknown scenario_id 'x'. Valid options: ..."}
Missing body field  → 422 (Pydantic validation)
Missing dataset     → 503 with {"detail": "Dataset files not found. Run: ..."}
```

This is enforced by `TestErrorHandling` tests and the smoke test.

### The baseline endpoint is separate from the planning endpoint

`GET /api/scenarios/{id}/baseline` runs ETA estimation, demand simulation,
and alert generation but **not the planner search**. The frontend uses this
to populate the dashboard immediately on scenario selection, before the user
clicks "Run Planning". The planner (which runs 54 candidates) only triggers
on explicit user action.

---

## 13. Frontend Architecture

### State flow

```
App mounts
  → fetchHealth()          → header: API ✓ online
  → fetchNetwork()         → metric cards, RouteTable, BusTable
  → fetchScenarios()       → ScenarioSelector buttons
  → fetchBaseline("normal") → EtaTable, AlertList populated

User clicks scenario
  → fetchBaseline(scenarioId) → updates EtaTable + AlertList

User clicks "Run Planning"
  → postPlan({scenario_id})
  → RecommendationPanel appears
  → ComparisonPanel appears

User clicks "Reset"
  → clears plan result
  → reloads baseline
```

### Why no state management library?

The state is simple: a few async-loaded objects, one selected scenario, one
optional plan result. React's built-in `useState` + `useEffect` + `useCallback`
handles it without Redux/Zustand. Adding a state library would increase
bundle size and cognitive overhead for no benefit.

### Why plain CSS instead of a component library?

The spec said "lightweight"; the UI prioritises the planning workflow over
decorative graphics. Plain CSS means zero runtime overhead, full control over
every visual decision, and no version conflicts. The entire stylesheet is
under 400 lines.

### `VITE_API_URL` environment variable

```typescript
const BASE_URL = (import.meta.env.VITE_API_URL as string | undefined)
  ?? 'http://localhost:8000/api';
```

For local dev: defaults to `http://localhost:8000/api`.
For Docker: injected as a build arg (`VITE_API_URL=/api`), pointing to the
Nginx proxy path which forwards to the backend container.

---

## 14. Testing Strategy — 265 Tests

### Test distribution

| File | Tests | What it covers |
|------|------:|----------------|
| `test_dataset.py` | 45 | Determinism, ID integrity, field constraints, GPS ordering, ground-truth isolation, scenario transforms |
| `test_eta.py` | 27 | Chronological order, window validity, estimate source, delay propagation, MAE/coverage |
| `test_constraints.py` | 23 | Demand simulation correctness, served+unmet=total, alert generation |
| `test_metrics.py` | 33 | C1–C7 validator, waiting time, fleet utilisation, ETA quality, objective cost, deltas |
| `test_planner.py` | 29 | Candidate generation, accepted plans have zero violations, determinism, all 4 scenarios |
| `test_api.py` | 57 | Full HTTP contract, error codes, no hardcoded metrics, weight override, comparison |
| `test_edge_cases.py` | 51 | Dataset reset, zero-baseline division, ETA denominator, circular imports, typed-object inputs |

### The ground-truth isolation test

```python
def test_eta_service_does_not_reference_ground_truth(self):
    path = Path("app/services/eta_service.py")
    import_lines = [l for l in path.read_text().splitlines()
                    if l.strip().startswith(("import ", "from "))]
    assert "ground_truth" not in "\n".join(import_lines)
```

This scans only import statements (not docstrings that mention isolation)
to prevent false positives.

### Why test the import block instead of the whole file?

Earlier versions of this test scanned the entire file and failed because the
docstring said "This module never imports load_ground_truth()". Scanning only
import lines is both more precise and immune to self-referential docstrings.

### `conftest.py` — session-scoped fixtures

The dataset generator takes ~0.1 seconds. Without session-scoped fixtures,
every test module would re-generate the dataset. With `conftest.py`, the
generator runs **once** per `pytest` session and the result is shared.

---

## 15. Deployment Readiness

### Environment variable surface area

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOST` | `0.0.0.0` | Uvicorn bind address |
| `PORT` | `8000` | Uvicorn port |
| `WORKERS` | `1` | Uvicorn workers |
| `LOG_LEVEL` | `info` | Logging verbosity |
| `CORS_ORIGINS` | localhost origins | Comma-separated allowed origins |
| `RANDOM_SEED` | `42` | Dataset generator seed |
| `WEIGHT_WAITING_TIME` | `1.0` | Objective weight |
| `WEIGHT_UNMET_PASSENGERS` | `5.0` | Objective weight |
| `WEIGHT_MISSED_CONNECTIONS` | `10.0` | Objective weight |
| `VITE_API_URL` | `http://localhost:8000/api` | Frontend → backend URL |

### What a production deployment looks like

```
Internet
   │
   ▼
Nginx (port 80)
   ├── /          → serves frontend static files (Vite dist/)
   └── /api/*     → proxy_pass to FastAPI backend (port 8000)

FastAPI (port 8000)
   ├── Reads CORS_ORIGINS from environment
   ├── Auto-generates dataset at startup if missing
   └── Uses named Docker volume to persist generated/ across restarts
```

### `.gitignore` coverage

- `__pycache__/`, `.pytest_cache/`, `*.pyc`
- `backend/app/data/generated/*.csv` and `*.json` (regeneratable)
- `node_modules/`, `frontend/dist/`, `*.tsbuildinfo`
- `.env`, `.env.*` (all environment files with secrets)
- `.vscode/`, `.idea/`, `.DS_Store`

---

## 16. Technology Choices — Why Each One

| Technology | Why chosen | What was considered |
|-----------|-----------|---------------------|
| **FastAPI** | Automatic Pydantic validation, excellent OpenAPI docs, async-ready, simple testing with `TestClient` | Flask (no automatic validation), Django (too heavy for an API-only service) |
| **Pydantic v2** | Request/response validation at the boundary; Pydantic models double as in-memory types | dataclasses (no validation), attrs (less ecosystem integration) |
| **Python stdlib only** (no Pandas in core) | Small dataset; Pandas adds a large dependency for simple CSV reads | Pandas was in original requirements but removed when it conflicted on the test machine |
| **React + TypeScript** | TypeScript interfaces directly mirror Pydantic schemas; catches API contract mismatches at build time | Vue, Svelte (less familiar; TypeScript integration less mature at time of spec) |
| **Vite** | Sub-second dev rebuild; built-in TypeScript and JSX support | Create React App (deprecated), Webpack (more configuration) |
| **Plain CSS** | Zero runtime overhead; full control; no version conflicts | Tailwind (more setup), MUI (heavier bundle) |
| **pytest** | Industry standard; excellent parametrize support; `TestClient` integration | unittest (more verbose) |
| **No external database** | MVP spec explicitly required no external DB; all data is reproducible from seed | SQLite (unnecessary for in-memory data), PostgreSQL (too heavy) |
| **No auth/payments** | Explicitly out of scope in spec §2.2 | — |

---

## 17. Limitations

State these **proactively** — it shows technical honesty, which judges and
interviewers value more than pretending everything is production-perfect.

1. **Synthetic data** — 8 stops, 4 buses, 3 routes at illustrative London
   coordinates. Not a real campus fleet.
2. **Fixed route sequences** — the planner cannot redesign routes, only
   reassign buses or shift departure times.
3. **Single-trip changes only** — 54 candidates, not combinatorial multi-trip
   optimisation. A joint shift of two trips might be better.
4. **Demand model is approximate** — passengers are assumed to arrive exactly
   at `time_window_start_min`. Real passengers arrive with a distribution.
5. **No live GPS** — ETA accuracy is measured against simulation ground
   truth, not real-world arrivals. GPS MAE is meaningful as a system-test
   but not as a real-world accuracy claim.
6. **Missed connections not modelled** — the weight exists in the objective
   function but no connection timetable is in the dataset. Always `0`.
7. **Waiting time formula creates 0 wait in normal scenario** — because
   demand windows exactly match scheduled departures. Waiting time only
   becomes non-zero when departures are shifted. This is documented in
   `assumptions.md`.
8. **No authentication or user accounts** — single-operator tool, no
   multi-user state.
9. **Repositioning buffer is conservative and uniform** — 10 minutes for
   all bus pairs regardless of distance. Real systems would use a
   stop-to-stop travel-time matrix.

---

## 18. Interview Questions & Model Answers

### "Walk me through the architecture."

> "There are three layers. The data layer generates a deterministic synthetic
> dataset using seed 42 — 8 stops, 4 buses, 3 routes, 6 trips. The backend
> is FastAPI: it loads the dataset once at startup into app state, then serves
> six API endpoints. The core logic is in six services — ETA estimation,
> demand simulation, alert generation, constraint validation, planning, and
> metrics. The frontend is React with TypeScript, fetching from the API and
> rendering the results. Nothing is hardcoded — every number you see was
> computed from a real service call."

### "Why didn't you use a database?"

> "The spec explicitly ruled it out. The dataset is deterministically generated
> from a fixed seed and fits entirely in memory. There's no user state, no
> write operations, and no persistence requirement beyond the generated files.
> Adding a database would introduce infrastructure complexity with zero benefit
> for this use case."

### "How do you ensure the comparison is fair between baseline and proposed?"

> "Both plans are evaluated using the same scenario-transformed demand and the
> same disruption inputs. The `build_scenario_dataset()` function applies
> multipliers and delays once, and that transformed dataset is passed to both
> the baseline evaluation and the candidate search. The planner cannot give
> the proposed plan easier test conditions."

### "What happens if no improvement is possible?"

> "The system reports `no_improvement` with the baseline metrics and an
> explanation. It never fabricates an improvement. For `vehicle_unavailable`,
> the baseline itself is infeasible (C2 violation), so the system reports
> `infeasible_baseline` and still attempts to find a valid alternative — if
> it finds one, it shows that too."

### "How does the ETA estimator avoid using ground truth?"

> "The `eta_service.py` module never imports `load_ground_truth()`. This is
> enforced by a source-scan test that reads the import statements of the file
> and fails if `ground_truth` appears in them. Ground truth is only accessible
> from `metrics_service.py`, which is used purely for evaluation after the
> fact — never as input to the estimator."

### "Walk me through constraint C3 and C4 — what's the difference?"

> "C3 checks for time overlap: if two trips assigned to the same bus have
> overlapping time intervals, C3 fires. C4 checks for insufficient gap: even
> if trips don't overlap, there must be at least 10 minutes between the end
> of one trip and the start of the next for the driver to reposition. A trip
> pair could pass C3 but still fail C4 if the gap is, say, 6 minutes."

### "How many test cases do you have and how are they structured?"

> "265 tests across 7 files. They follow a phase structure: data integrity
> in Phase 1, ETA and demand in Phase 2, constraint validator and planner in
> Phase 3, API contract in Phase 4, and edge cases including module import
> regression guards in Phase 6. The most important test is the parametrized
> `test_accepted_plan_zero_violations` which runs the full planner for all four
> scenarios and asserts that every accepted plan passes C1 through C7."

### "What would you do differently if this were a real production system?"

> "Several things: I'd replace the synthetic data with real GPS telemetry and
> actual passenger count data. I'd add a persistent database to store
> historical trip data for better ETA estimates — right now we have at most
> two GPS observations per route segment. I'd model the repositioning buffer
> as a stop-to-stop travel-time matrix rather than a uniform 10-minute
> constant. I'd add authentication for multi-operator use. And I'd extend the
> planner to consider multi-trip joint changes."

---

## 19. Hackathon Judging Questions & Model Answers

### "Is this just a map that shows bus locations?"

> "No — that's a common tool and not what this does. RoutePulse is a planning
> recommendation engine. It detects problems, searches for feasible fixes,
> validates every fix against seven hard constraints, and shows measurable
> before-and-after results. The core value proposition is explainability and
> constraint awareness, not visualisation."

### "The numbers on the dashboard — are they real or hardcoded?"

> "They're computed from the scenario data. You can verify this: switch from
> the Normal scenario to Demand Spike and watch the unmet passengers counter
> go from 0 to 36, and the objective cost jump from 0 to 180. That's
> `5.0 × 36 = 180.0` — the exact formula is documented. If you change the
> `unmet_passengers` weight to 100 via the API, the cost becomes `100 × 36 = 3600`.
> Every number traces back to a formula."

### "What does 'constraint-aware' actually mean?"

> "Every candidate the planner generates is validated against seven rules
> before it can be accepted. For example, in `vehicle_unavailable`, the
> planner tries to reassign Trip T3 to another bus. It won't accept a
> candidate that assigns Bus B1 — because B1 is marked unavailable — and it
> won't accept a reassignment that creates a time overlap with another trip on
> the same bus. Only candidates that pass all seven constraints are considered.
> If none pass, the system explicitly reports infeasibility."

### "How do you handle the case where there's no good solution?"

> "The system has three outcomes. `improvement_found` means a feasible
> candidate with lower cost was found. `no_improvement` means every feasible
> candidate either ties or is worse than the baseline — we report the baseline
> as the best available plan. `infeasible_baseline` means even the current
> plan has violations — we still search and report whatever feasible plan we
> find, or honestly state that none exists."

### "How is this different from a simple greedy algorithm?"

> "It is a bounded exhaustive search over a small deterministic candidate
> space rather than greedy. Greedy might take the first improvement it finds;
> this system evaluates all 54 candidates, rejects those with constraint
> violations, and picks the one with the lowest objective cost. That means
> it finds the globally optimal single-trip change within the search space,
> not just the first one that looks good."

### "What's the hardest technical problem you solved?"

> "Probably the boarding simulation bug. The original implementation computed
> `seats_left = capacity - sum(onboard) - boarded` where `boarded` was a
> running counter for the current stop. After the first group boarded and
> was added to `onboard`, the next group's `seats_left` was being reduced
> twice — once from the `onboard` sum and once from `boarded`. The fix was
> to always derive `seats_left` purely from `onboard`, which is updated
> immediately when a group boards. A test caught this: D05 (36 pax) would
> board, and then D07 (15 pax) showed only 14 seats remaining but the code
> was computing -22."

---

## 20. Key Numbers to Remember

| Number | Meaning |
|--------|---------|
| `42` | Random seed — all data is deterministic from this |
| `8` | Stops in the dataset |
| `4` | Buses: B1=50, B2=40, B3=30, B4=20 seats |
| `3` | Routes: R1 Campus Loop, R2 Central Express, R3 Residential Link |
| `6` | Scheduled trips (T1–T6) |
| `13` | Demand records (D01–D13) |
| `38` | R2 08:00 passengers in normal scenario (fits in B1's 50 seats) |
| `69` | R2 08:00 passengers in demand_spike (exceeds B1's 50 seats) |
| `36` | Unmet passengers in demand_spike baseline |
| `180.0` | demand_spike baseline objective cost = 5.0 × 36 unmet |
| `3600.0` | demand_spike cost with unmet_passengers weight = 100.0 |
| `54` | Candidates generated per planning run (6 trips × 9 changes) |
| `10 min` | Repositioning buffer required between consecutive trips (C4) |
| `±10 min` | T3's permitted departure window (tightest in the dataset) |
| `±15 min` | All other trips' permitted departure windows |
| `5 min` | Departure shift step size |
| `3` | Minimum GPS samples needed to use median (else fall back) |
| `±0.3 min` | GPS timestamp noise bound |
| `±2.0 min` | ETA fallback window when GPS samples are insufficient |
| `7` | Hard constraint codes (C1–C7) |
| `265` | Total automated tests |
| `45/45` | Smoke test checks passed against live server |
| `1.0 / 5.0 / 10.0` | Default objective weights (waiting / unmet / missed) |
