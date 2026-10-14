"""
Alert generation service for RoutePulse.

Produces operational alerts from ETA predictions and demand simulation results.
Alerts are informational — they do not block a plan (hard constraints do that).
They drive the dashboard's Alerts panel.

Alert categories (spec §2.1):
  - OVERLOAD:         onboard load exceeded bus capacity at a stop.
  - UNMET_DEMAND:     passengers could not board due to capacity limits.
  - LATE_ARRIVAL:     expected ETA exceeds nominal schedule by > threshold.
  - BUS_UNAVAILABLE:  a trip is assigned a bus marked unavailable.
  - DATA_MISSING:     a trip references an unknown route or bus.

Late-arrival threshold: 2 minutes above nominal.  Minor GPS noise does not
trigger this; genuine scenario delays (bus_delay = +10 min) will.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.schemas import (
    Bus,
    DemandResult,
    EtaPrediction,
    Route,
    ScenarioDefinition,
    Trip,
)
from app.data.generate_dataset import _route_stop_arrivals


# Minutes over scheduled arrival before a stop is flagged "late"
LATE_THRESHOLD_MIN: float = 2.0

AlertSeverity = Literal["warning", "critical"]


# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    code: str
    severity: AlertSeverity
    trip_id: str | None
    stop_id: str | None
    message: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "trip_id": self.trip_id,
            "stop_id": self.stop_id,
            "message": self.message,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Individual alert generators
# ---------------------------------------------------------------------------

def _capacity_alerts(demand_results: list[DemandResult]) -> list[Alert]:
    """
    OVERLOAD: any stop where onboard > capacity.
    UNMET_DEMAND: any trip that left passengers behind.
    """
    alerts: list[Alert] = []
    for result in demand_results:
        for event in result.stop_events:
            if event.overloaded:
                excess = event.onboard_after - event.capacity
                alerts.append(Alert(
                    code="OVERLOAD",
                    severity="critical",
                    trip_id=event.trip_id,
                    stop_id=event.stop_id,
                    message=(
                        f"Trip {event.trip_id}: capacity exceeded at stop "
                        f"{event.stop_id} "
                        f"({event.onboard_after}/{event.capacity} seats)."
                    ),
                    detail=f"Excess passengers: {excess}.",
                ))
        if result.total_unmet > 0:
            alerts.append(Alert(
                code="UNMET_DEMAND",
                severity="warning",
                trip_id=result.trip_id,
                stop_id=None,
                message=(
                    f"Trip {result.trip_id}: {result.total_unmet} passenger(s) "
                    f"could not board (capacity {result.capacity} seats, "
                    f"max load {result.max_load})."
                ),
                detail=(
                    f"Served: {result.total_served}. "
                    f"Unmet: {result.total_unmet}."
                ),
            ))
    return alerts


def _late_arrival_alerts(
    eta_predictions: dict[str, list[EtaPrediction]],
    trips: list[Trip],
    routes: list[Route],
    scenario: ScenarioDefinition,
) -> list[Alert]:
    """
    LATE_ARRIVAL: expected ETA exceeds nominal (no-delay) schedule arrival by
    more than LATE_THRESHOLD_MIN minutes.

    Nominal arrivals use only configured segment travel times (no scenario
    delays), so any delay injected by the scenario is correctly surfaced.
    """
    alerts: list[Alert] = []
    route_map: dict[str, Route] = {r.route_id: r for r in routes}

    for trip in trips:
        preds = eta_predictions.get(trip.trip_id)
        if not preds:
            continue
        route = route_map.get(trip.route_id)
        if not route:
            continue

        # Build nominal arrivals from configured segment times only
        route_dict = {
            "stop_ids": route.stop_ids,
            "segments": [
                {
                    "from_stop_id": s.from_stop_id,
                    "to_stop_id": s.to_stop_id,
                    "travel_time_min": s.travel_time_min,
                }
                for s in route.segments
            ],
        }
        nominal: dict[str, float] = dict(
            _route_stop_arrivals(route_dict, trip.scheduled_departure_min)
        )

        for pred in preds:
            nom = nominal.get(pred.stop_id)
            if nom is None:
                continue
            lateness = pred.expected_arrival_min - nom
            if lateness > LATE_THRESHOLD_MIN:
                alerts.append(Alert(
                    code="LATE_ARRIVAL",
                    severity="warning",
                    trip_id=trip.trip_id,
                    stop_id=pred.stop_id,
                    message=(
                        f"Trip {trip.trip_id}: expected late at stop "
                        f"{pred.stop_id} by {lateness:.1f} min "
                        f"(nominal {nom:.0f}, predicted "
                        f"{pred.expected_arrival_min:.1f})."
                    ),
                    detail=(
                        f"Nominal arrival: {nom:.1f} min from midnight. "
                        f"Predicted: {pred.expected_arrival_min:.1f} min. "
                        f"Lateness: {lateness:.1f} min."
                    ),
                ))

    return alerts


def _data_quality_alerts(
    trips: list[Trip],
    routes: list[Route],
    buses: list[Bus],
) -> list[Alert]:
    """
    BUS_UNAVAILABLE: assigned bus is marked unavailable in this scenario.
    DATA_MISSING: trip references unknown route or bus.
    """
    alerts: list[Alert] = []
    route_ids = {r.route_id for r in routes}
    bus_map: dict[str, Bus] = {b.bus_id: b for b in buses}

    for trip in trips:
        if trip.route_id not in route_ids:
            alerts.append(Alert(
                code="DATA_MISSING",
                severity="critical",
                trip_id=trip.trip_id,
                stop_id=None,
                message=(
                    f"Trip {trip.trip_id}: references unknown route "
                    f"'{trip.route_id}'."
                ),
            ))
        bus = bus_map.get(trip.bus_id)
        if bus is None:
            alerts.append(Alert(
                code="DATA_MISSING",
                severity="critical",
                trip_id=trip.trip_id,
                stop_id=None,
                message=(
                    f"Trip {trip.trip_id}: references unknown bus "
                    f"'{trip.bus_id}'."
                ),
            ))
        elif bus.status == "unavailable":
            alerts.append(Alert(
                code="BUS_UNAVAILABLE",
                severity="critical",
                trip_id=trip.trip_id,
                stop_id=None,
                message=(
                    f"Trip {trip.trip_id}: bus {trip.bus_id} is unavailable "
                    f"in this scenario (hard constraint C2 violated)."
                ),
                detail="Bus must be reassigned before the plan is accepted.",
            ))

    return alerts


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_alerts(
    trips: list[Trip],
    routes: list[Route],
    buses: list[Bus],
    demand_results: list[DemandResult],
    eta_predictions: dict[str, list[EtaPrediction]],
    scenario: ScenarioDefinition,
) -> list[Alert]:
    """
    Generate all operational alerts for a plan.

    Returns a list sorted: critical first, then by trip_id alphabetically.
    """
    alerts: list[Alert] = []
    alerts.extend(_capacity_alerts(demand_results))
    alerts.extend(_late_arrival_alerts(eta_predictions, trips, routes, scenario))
    alerts.extend(_data_quality_alerts(trips, routes, buses))

    severity_order = {"critical": 0, "warning": 1}
    alerts.sort(
        key=lambda a: (severity_order.get(a.severity, 9), a.trip_id or "")
    )
    return alerts
