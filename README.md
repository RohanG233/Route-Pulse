# RoutePulse

A campus transport planning tool for operators. RoutePulse loads routes, stops,
buses, schedules, passenger demand, and simulated GPS traces; estimates arrival
windows; detects capacity and timetable problems; evaluates feasible operational
changes; and compares the selected plan with the original baseline.

> **All data is synthetic and labelled as such. This is a hackathon MVP.**

---

## Quick start (fresh clone)

### Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11 + |
| Node.js | 18 + |

### 1 — Install backend dependencies

```bash
cd backend
python -m pip install -r requirements.txt
```

### 2 — Generate the dataset

```bash
cd backend
python -m app.data.generate_dataset
```

Files are written to `backend/app/data/generated/`.
Re-running with the same seed (42) always produces identical files — this is
the reset mechanism for the demo.

### 3 — Run backend tests

```bash
cd backend
python -m pytest              # uses pytest.ini; discovers app/tests/
```

All 270 + tests should pass. A quick summary run:

```bash
cd backend
python run_tests.py
```

### 4 — Start the backend server

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

The server auto-generates the dataset at startup if the files are missing.
Health check: `http://localhost:8000/api/health`

### 5 — Install and start the frontend

```bash
cd frontend
# Windows (PowerShell execution policy may block .ps1 scripts; use cmd.exe or):
node "C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js" install
node "C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js" run dev

# macOS / Linux
npm install
npm run dev
```

Open `http://localhost:5173` in a browser.

### 6 — Verify end-to-end

1. The header shows **API ✓ online**.
2. Select **Demand Spike** — the ETA table and alert list populate.
3. Click **Run Planning / Optimise** — the recommendation and comparison panels appear.
4. The comparison panel shows computed before/after metrics (not placeholders).

---

## Project structure

```
routepulse/
├── README.md
├── docs/
│   ├── assumptions.md       # All design decisions and formula documentation
│   └── demo_script.md       # 2–3 minute operator demo walkthrough
├── backend/
│   ├── requirements.txt
│   ├── pytest.ini           # Test discovery config
│   ├── run_tests.py         # Helper: clear cache + run all test files
│   └── app/
│       ├── config.py        # Single source of truth for all constants
│       ├── schemas.py       # Pydantic models for every API shape
│       ├── main.py          # FastAPI app (lifespan, CORS, error handlers)
│       ├── data/
│       │   ├── generate_dataset.py   # Deterministic synthetic data generator
│       │   ├── scenarios.py          # 4 scenario definitions + transforms
│       │   └── generated/            # Output CSV/JSON (re-generatable)
│       ├── services/
│       │   ├── data_loader.py        # Typed file readers
│       │   ├── eta_service.py        # GPS-median ETA estimator (no ground truth)
│       │   ├── demand_service.py     # Stop-by-stop boarding/alighting simulation
│       │   ├── alert_service.py      # Operational alert generation
│       │   ├── constraint_validator.py  # Hard constraints C1–C7
│       │   ├── planner_service.py    # Candidate search + PlanResponse assembly
│       │   └── metrics_service.py    # All metric computations + deltas
│       ├── routers/
│       │   ├── health.py    # GET /api/health
│       │   ├── network.py   # GET /api/network, /api/scenarios, /api/scenarios/{id}/baseline
│       │   └── planning.py  # POST /api/plan, GET /api/scenarios/{id}/comparison
│       └── tests/
│           ├── conftest.py           # Shared fixtures (session-scoped dataset)
│           ├── test_dataset.py       # Phase 1: data integrity (45 tests)
│           ├── test_eta.py           # Phase 2: ETA order, quality, GPS samples (27 tests)
│           ├── test_constraints.py   # Phase 2: demand simulation, alerts (23 tests)
│           ├── test_metrics.py       # Phase 3: constraint validator C1–C7, metrics (33 tests)
│           ├── test_planner.py       # Phase 3: candidate gen, planning, determinism (29 tests)
│           ├── test_api.py           # Phase 4: API contract, error handling (57 tests)
│           └── test_edge_cases.py    # Phase 6: hardening, regression guards (60 + tests)
└── frontend/
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── types.ts          # TypeScript interfaces matching Pydantic schemas
        ├── api/client.ts     # Typed fetch wrappers for all endpoints
        ├── App.tsx           # Dashboard shell (health check → load → plan → compare)
        ├── styles.css        # Responsive plain CSS
        └── components/
            ├── ScenarioSelector.tsx   # 4-button picker with description
            ├── MetricCard.tsx         # Reusable metric tile
            ├── RouteTable.tsx         # Trip schedule with load/status
            ├── BusTable.tsx           # Fleet with availability and status badge
            ├── EtaTable.tsx           # Per-stop ETA with window and source
            ├── AlertList.tsx          # Severity-coded operational alerts
            ├── RecommendationPanel.tsx # Action detail + constraint violations
            └── ComparisonPanel.tsx    # 3-column baseline / proposed / delta
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | `{"status":"ok"}` liveness check |
| GET | `/api/network` | Buses, stops, routes, trips, seed, version |
| GET | `/api/scenarios` | List of 4 scenario summaries |
| GET | `/api/scenarios/{id}/baseline` | ETA + demand + alerts (no planner search) |
| POST | `/api/plan` | Full planning cycle → `PlanResponse` |
| GET | `/api/scenarios/{id}/comparison` | Deterministic re-computation of POST /api/plan |

All errors return `{"detail": "..."}` — never raw Python tracebacks.

---

## Scenarios

| ID | Name | What it tests |
|----|------|---------------|
| `normal` | Normal | Clean baseline; all buses available, normal demand |
| `demand_spike` | Demand Spike | Route R2 demand ×1.8 — overloads bus B1 on trip T3 |
| `bus_delay` | Bus Delay | Trip T3 delayed +10 min on every segment |
| `vehicle_unavailable` | Vehicle Unavailable | Bus B1 removed; planner must find alternative |

---

## Objective weights (documented defaults)

```
cost = (1.0 × total_waiting_minutes)
     + (5.0 × unmet_passengers)
     + (10.0 × missed_connections)
```

Configured in `backend/app/config.py → DEFAULT_OBJECTIVE_WEIGHTS`.
Override per request via the API:

```json
POST /api/plan
{
  "scenario_id": "demand_spike",
  "objective_weights": { "waiting_time": 1.0, "unmet_passengers": 20.0, "missed_connections": 10.0 }
}
```

---

## Hard constraints (C1–C7)

Every candidate plan is validated against all seven constraints. **Any violation
rejects the candidate.** The objective function cannot override a hard constraint.

| Code | Rule |
|------|------|
| C1 | Onboard passengers ≤ bus capacity at every stop |
| C2 | Trip starts/ends within bus availability window; bus not unavailable |
| C3 | A bus cannot serve two time-overlapping trips |
| C4 | ≥ 10-minute repositioning gap between consecutive trips on the same bus |
| C5 | Departure shift within the trip's `permitted_departure_window_min` |
| C6 | Route stop order valid; all segment travel times positive; arrival > departure |
| C7 | All referenced buses, routes, and stops exist; demand OD order valid |

---

## Reset / regenerate demo

```bash
cd backend
python -m app.data.generate_dataset   # overwrites generated/ with fresh identical data
python -m pytest                       # all tests must still pass
```

---

## Implementation phases

| Phase | Content | Tests | Status |
|-------|---------|-------|--------|
| 1 | Data scaffold, generator (seed 42), 4 scenarios, validation | 45 | ✅ |
| 2 | ETA service, demand/load simulation, alert generation | 27 + 23 | ✅ |
| 3 | Constraint validator (C1–C7), planner, metrics, deltas | 33 + 29 | ✅ |
| 4 | FastAPI routers, Pydantic contracts, API error handling | 57 | ✅ |
| 5 | React + TypeScript + Vite dashboard (all 8 components) | build ✓ | ✅ |
| 6 | Demo hardening, edge-case tests, README, smoke-test | 60 + | ✅ |

---

## Limitations (honest, from the spec)

- **Synthetic campus data** — not a real fleet; labelled as such throughout.
- **Fixed route sequences** — dynamic re-routing is out of scope.
- **Single-trip changes only** — the planner modifies one trip at a time.
- **No live GPS** — ETA accuracy is validated against simulation ground truth only.
- **Missed connections not modelled** — reported as `"not_modelled"`.
- **Waiting-time formula is an approximation** — passengers assumed to arrive
  at their `time_window_start_min`. Applied identically to baseline and proposed.
- **No external services required** — no API keys, paid services, or deployment.

See `docs/assumptions.md` for full formula documentation.

---

## 2–3 minute demo script

See `docs/demo_script.md` for the full walkthrough.
