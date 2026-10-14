# RoutePulse — Assumptions and Design Decisions

This document records every deliberate assumption made during implementation,
per spec section 18. Update it whenever a design choice is made that is not
fully specified in the source document.

---

## 1. Data model

### 1.1 Time representation
All time values are stored as **minutes from midnight** (integers or floats).
Conversion to `HH:MM` strings happens only at API/UI boundaries using
`min_to_hhmm()` in `schemas.py`.

**Why:** Avoids time-zone complexity; arithmetic is straightforward.

### 1.2 Bus capacity ordering
Buses are assigned capacities in descending order: B1=50, B2=40, B3=30, B4=20.
B1 (50 seats) is the largest bus and is critical for the R2 peak demand window.

**Why:** The demand-spike scenario is designed so that R2 at 08:00 carries 38
passengers in the normal scenario (within B1's 50-seat limit) but ~69 after the
×1.8 multiplier (exceeding B1's capacity). This makes the overload detectable
and gives the planner a meaningful problem to solve.

### 1.3 Segment travel times
Route segment travel times are **fixed constants**, not randomly generated.
They are defined in `generate_dataset.py → SEGMENT_TIMES`.

**Why:** Fixed values ensure the dataset is fully deterministic without needing
to re-derive travel times from noisy GPS traces. The GPS traces add noise on
top of these constants.

---

## 2. Dataset generation

### 2.1 GPS trace noise
GPS timestamp noise is bounded to ±`GPS_NOISE_BOUND` minutes (default 0.3 min)
and coordinate noise is ±0.0002 degrees.

**Why:** Small enough that the ETA estimator produces a meaningful MAE (not
trivially zero), but small enough to keep routes visually plausible. The spec
requires "bounded variability so ETA error is meaningful but reproducible."

### 2.2 GPS samples never precede trip start
Noisy timestamps are clamped to `max(trip_start, noisy_t)`. They are not
clamped at the end — a noisy sample could theoretically land slightly after
the true end. This is acceptable because the ETA estimator uses segment median
estimates, not raw endpoint times.

### 2.3 Ground truth at first stop = departure time
The first stop's ground-truth arrival equals the scheduled departure time.
**Assumption:** The bus is at the origin stop at departure time.

---

## 3. Demand model

### 3.1 Demand window matching
A demand record is served by a trip if:
- The demand's `route_id` matches the trip's `route_id`.
- The demand's `time_window_start_min` equals the trip's `scheduled_departure_min`.

**Assumption:** Each demand window corresponds to exactly one trip in the
timetable. This keeps the matching deterministic and avoids ambiguous
multi-trip assignment. Document this in the demand_service when implemented.

### 3.2 Fractional passenger rounding
When a demand multiplier produces fractional passengers, the result is rounded
**up** (ceiling) to avoid losing partial groups.

**Example:** 20 × 1.8 = 36.0 (no rounding needed), 8 × 1.8 = 14.4 → 15.

### 3.3 Boarding allocation rule (Phase 2, documented here)
When remaining capacity < waiting passengers, board passengers in
**demand_id lexicographic order** until the bus is full. Remaining passengers
are counted as unmet demand. This is deterministic and explicit.

---

## 4. ETA estimation (Phase 2)

### 4.1 Fallback window
When fewer than `MIN_SAMPLES_FOR_MEDIAN` (3) GPS samples exist for a segment,
the estimator falls back to the configured segment travel time and reports
`estimate_source = "fallback_configured"`. The arrival window is ±`ETA_FALLBACK_WINDOW_MIN` (2.0 min).

### 4.2 Median vs. mean
The spec mandates median for robustness against outliers. GPS noise is bounded
so outliers are small, but median is used regardless for correctness.

### 4.3 Ground truth isolation
`load_ground_truth()` is only callable from `metrics_service.py` and test code.
The `eta_service.py` module must never import it. This is enforced by
`test_dataset.py::TestGroundTruthIsolation`.

---

## 5. Planning

### 5.1 Candidate space
Each candidate modifies exactly one trip: either reassigning its bus or
shifting its departure time in `DEPARTURE_SHIFT_STEP_MIN` (5 min) increments,
up to `MAX_DEPARTURE_SHIFT_MIN` (15 min) in either direction.

**Why:** The spec says "one trip's bus and/or departure time at a time." Keeping
changes minimal makes explanations clearer and validation cheaper.

### 5.2 Tie-breaking
When multiple feasible candidates share the lowest objective cost, the one
with fewer changes wins. Further ties are broken lexicographically by trip ID.

### 5.3 Objective weights
| Metric | Default weight |
|--------|---------------|
| total_waiting_minutes | 1.0 |
| unmet_passengers | 5.0 |
| missed_connections | 10.0 |

**Why these weights:** Unmet passengers are penalised 5× more than a minute of
waiting because a missed ride is much worse than a short delay. Missed
connections (when modelled) are penalised most heavily.

These are MVP defaults, not validated operational values.

### 5.4 Missed connections
No connection timetable is modelled in Phase 1–3. The metric is reported as
`"not_modelled"` throughout. The weights still appear in config for when
connection data is added.

---

## 6. Metrics

### 6.1 Waiting time formula
For a demand record served by a trip:

```
wait = max(0, trip_departure_min - demand_time_window_start_min)
```

This is a deterministic approximation that assumes passengers arrive at the
stop exactly at their `time_window_start_min`. The same formula is applied to
both baseline and proposed plans so comparisons are fair.

### 6.2 Fleet utilization
```
utilization = total_assigned_service_minutes / total_available_bus_minutes
```

Where:
- `total_assigned_service_minutes` = sum of (trip_end_min − trip_departure_min) for each assigned trip.
- `total_available_bus_minutes` = sum of (available_until_min − available_from_min) for each bus in the scenario.

Unavailable buses contribute 0 to the numerator but 0 to the denominator (they
are excluded from the available pool in that scenario).

### 6.3 Zero-baseline division
When the baseline value of a metric is zero, the improvement percentage is
reported as `None` (displayed as "N/A" in the UI) rather than dividing by zero.

---

## 7. Scenarios

### 7.1 Uniform segment delay
The `bus_delay` scenario adds `BUS_DELAY_MINUTES` to **every** segment of the
affected trip (T3). The spec says "selected route segment"; applying it uniformly
is the conservative assumption and is explicitly documented here.

### 7.2 Symmetric scenario inputs
Both the baseline plan and the proposed plan are evaluated against the same
transformed demand and disruptions. The proposed plan never receives easier
inputs than the baseline.

---

## 8. Out of scope (MVP)

- Real campus GPS / fleet integration
- Machine-learning demand forecasting
- Dynamic route redesign
- User accounts, authentication, or payments
- Production deployment, microservices, or external databases
- Mobile app or push notifications
