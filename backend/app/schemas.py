"""
Pydantic data models for RoutePulse.

These models are used for:
  - In-memory representation of loaded dataset entities
  - API request/response validation
  - Internal service interfaces

All time values are in minutes from midnight (integer) unless noted.
HH:MM string conversion happens only at API/UI boundary helpers below.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def min_to_hhmm(minutes: int) -> str:
    """Convert minutes-from-midnight to 'HH:MM' string."""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def hhmm_to_min(hhmm: str) -> int:
    """Convert 'HH:MM' string to minutes-from-midnight."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------------------
# Dataset entities
# ---------------------------------------------------------------------------

class Bus(BaseModel):
    """A campus bus with capacity and availability window."""

    bus_id: str = Field(..., description="Unique bus identifier, e.g. 'B1'")
    capacity: int = Field(..., gt=0, description="Seat capacity (positive integer)")
    available_from_min: int = Field(..., ge=0, description="Availability start (mins from midnight)")
    available_until_min: int = Field(..., ge=0, description="Availability end (mins from midnight)")
    status: str = Field(default="available", description="'available' or 'unavailable'")

    @model_validator(mode="after")
    def availability_window_valid(self) -> "Bus":
        if self.available_until_min <= self.available_from_min:
            raise ValueError(
                f"Bus {self.bus_id}: available_until_min must be > available_from_min"
            )
        return self


class Stop(BaseModel):
    """A campus bus stop."""

    stop_id: str = Field(..., description="Unique stop identifier, e.g. 'S1'")
    name: str
    latitude: float
    longitude: float


class RouteSegment(BaseModel):
    """Travel-time estimate for one consecutive stop pair on a route."""

    from_stop_id: str
    to_stop_id: str
    travel_time_min: float = Field(..., gt=0, description="Estimated travel time in minutes")


class Route(BaseModel):
    """An ordered sequence of stops with inter-stop travel-time estimates."""

    route_id: str
    name: str
    stop_ids: list[str] = Field(..., min_length=2, description="Ordered stop IDs")
    segments: list[RouteSegment] = Field(
        ..., description="One segment per consecutive stop pair"
    )

    @model_validator(mode="after")
    def segments_match_stops(self) -> "Route":
        expected = len(self.stop_ids) - 1
        if len(self.segments) != expected:
            raise ValueError(
                f"Route {self.route_id}: expected {expected} segments for "
                f"{len(self.stop_ids)} stops, got {len(self.segments)}"
            )
        for i, seg in enumerate(self.segments):
            if seg.from_stop_id != self.stop_ids[i]:
                raise ValueError(
                    f"Route {self.route_id} segment {i}: from_stop_id mismatch"
                )
            if seg.to_stop_id != self.stop_ids[i + 1]:
                raise ValueError(
                    f"Route {self.route_id} segment {i}: to_stop_id mismatch"
                )
        return self

    def total_travel_time(self) -> float:
        """Sum of all segment travel times in minutes."""
        return sum(s.travel_time_min for s in self.segments)


class Trip(BaseModel):
    """A scheduled service run: one bus on one route at a specific departure time."""

    trip_id: str
    route_id: str
    bus_id: str
    scheduled_departure_min: int = Field(..., ge=0)
    permitted_departure_window_min: int = Field(
        default=15, ge=0,
        description="Maximum shift from scheduled_departure in either direction"
    )
    service_date: str = Field(default="scenario_day")


class DemandRecord(BaseModel):
    """A passenger origin-destination request within a time window."""

    demand_id: str
    route_id: str
    boarding_stop_id: str
    alighting_stop_id: str
    time_window_start_min: int = Field(..., ge=0)
    passengers: int = Field(..., ge=0)


class GpsPoint(BaseModel):
    """One GPS sample for a bus on a trip."""

    trace_id: str
    bus_id: str
    timestamp_min: float = Field(..., ge=0)
    latitude: float
    longitude: float
    route_id: Optional[str] = None
    trip_id: Optional[str] = None


class GroundTruthArrival(BaseModel):
    """Actual simulated arrival at a stop for one trip (evaluation only)."""

    trip_id: str
    stop_id: str
    actual_arrival_min: float


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

class ScenarioDefinition(BaseModel):
    """
    Defines one named operational scenario.

    Transformations are applied deterministically to baseline demand and travel
    times. Both baseline and proposed plans use the same transformed inputs.
    """

    scenario_id: str
    name: str
    description: str
    demand_multiplier: float = Field(
        default=1.0, description="Global demand scale factor"
    )
    demand_overrides: dict[str, float] = Field(
        default_factory=dict,
        description="Per-route demand multiplier overrides, keyed by route_id"
    )
    segment_delay_overrides: dict[str, float] = Field(
        default_factory=dict,
        description="Extra delay (minutes) added to named trip segments, keyed by trip_id"
    )
    unavailable_bus_ids: list[str] = Field(
        default_factory=list,
        description="Bus IDs that must not be assigned during this scenario"
    )


# ---------------------------------------------------------------------------
# Plan intermediate structures
# ---------------------------------------------------------------------------

class TripAssignment(BaseModel):
    """Resolved assignment of a bus to a trip with computed arrivals."""

    trip_id: str
    route_id: str
    bus_id: str
    departure_min: int
    stop_arrivals: dict[str, float] = Field(
        ..., description="stop_id -> computed arrival minutes from midnight"
    )


class ConstraintViolation(BaseModel):
    """One hard-constraint violation found during validation."""

    code: str = Field(..., description="e.g. 'C1', 'C2', ..., 'C7'")
    message: str
    trip_id: Optional[str] = None
    bus_id: Optional[str] = None
    stop_id: Optional[str] = None


class ValidationResult(BaseModel):
    """Outcome of validating a candidate plan against all hard constraints."""

    passed: bool
    violations: list[ConstraintViolation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ETA output
# ---------------------------------------------------------------------------

class EtaPrediction(BaseModel):
    """Predicted arrival for one trip at one stop."""

    trip_id: str
    stop_id: str
    expected_arrival_min: float
    window_start_min: float
    window_end_min: float
    estimate_source: Literal["median_observed", "fallback_configured"]
    assumptions: str


class EtaQualityMetrics(BaseModel):
    """Aggregate ETA accuracy against ground truth."""

    mae_minutes: float = Field(..., description="Mean absolute error in minutes")
    evaluated_count: int = Field(..., description="Number of trip-stop pairs evaluated")
    window_coverage_pct: float = Field(
        ..., description="% of ground-truth arrivals inside predicted window"
    )


# ---------------------------------------------------------------------------
# Demand / capacity output
# ---------------------------------------------------------------------------

class StopLoadEvent(BaseModel):
    """Passenger load state at one stop during one trip."""

    trip_id: str
    stop_id: str
    boarded: int
    alighted: int
    onboard_after: int
    capacity: int
    overloaded: bool
    unmet_at_stop: int


class DemandResult(BaseModel):
    """Demand simulation outcome for one trip."""

    trip_id: str
    bus_id: str
    capacity: int
    stop_events: list[StopLoadEvent]
    total_served: int
    total_unmet: int
    max_load: int
    capacity_utilization: float  # max_load / capacity


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

class PlanRequest(BaseModel):
    """POST /api/plan request body."""

    scenario_id: str
    objective_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Optional override of default objective weights"
    )


class MetricSnapshot(BaseModel):
    """One set of computed plan metrics."""

    avg_waiting_time_min: float
    unmet_passengers: int
    capacity_violations: int
    fleet_utilization_pct: float
    eta_mae_min: Optional[float] = None
    eta_window_coverage_pct: Optional[float] = None
    missed_connections: str = Field(
        default="not_modelled",
        description="Count or 'not_modelled' if connection data absent"
    )
    objective_cost: float


class MetricDelta(BaseModel):
    """Difference between proposed and baseline for one metric."""

    metric: str
    baseline: Any
    proposed: Any
    absolute_delta: Optional[float] = None
    pct_change: Optional[float] = None   # None when baseline is zero or non-numeric


class RecommendedAction(BaseModel):
    """One planning action included in the proposed plan."""

    action_type: Literal["reassign_bus", "shift_departure"]
    trip_id: str
    original_bus_id: Optional[str] = None
    new_bus_id: Optional[str] = None
    original_departure_min: Optional[int] = None
    new_departure_min: Optional[int] = None
    reason: str


class PlanResponse(BaseModel):
    """POST /api/plan full response."""

    scenario_id: str
    recommendation_status: Literal[
        "improvement_found", "no_improvement", "infeasible_baseline"
    ]
    recommendation_summary: str
    actions: list[RecommendedAction] = Field(default_factory=list)
    constraint_validation: ValidationResult
    baseline_metrics: MetricSnapshot
    proposed_metrics: Optional[MetricSnapshot] = None
    metric_deltas: list[MetricDelta] = Field(default_factory=list)
    eta_quality: Optional[EtaQualityMetrics] = None
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class NetworkResponse(BaseModel):
    """GET /api/network response."""

    buses: list[Bus]
    stops: list[Stop]
    routes: list[Route]
    trips: list[Trip]
    dataset_seed: int
    dataset_version: str


class ScenarioSummary(BaseModel):
    """One entry in GET /api/scenarios response."""

    scenario_id: str
    name: str
    description: str


class HealthResponse(BaseModel):
    """GET /api/health response."""

    status: Literal["ok"]
