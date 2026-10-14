import type { Trip, Route, Bus, DemandResult } from '../types';
import { minToHHMM } from '../types';

interface Props {
  trips: Trip[];
  routes: Route[];
  buses: Bus[];
  /** Demand results from baseline endpoint — may be empty before first load. */
  demandResults: DemandResult[];
}

export function RouteTable({ trips, routes, buses, demandResults }: Props) {
  const routeMap = Object.fromEntries(routes.map((r) => [r.route_id, r]));
  const busMap = Object.fromEntries(buses.map((b) => [b.bus_id, b]));
  const demandMap = Object.fromEntries(demandResults.map((d) => [d.trip_id, d]));

  // Compute trip end time from route segment travel times
  function tripEndMin(trip: Trip): number {
    const route = routeMap[trip.route_id];
    if (!route) return trip.scheduled_departure_min;
    const totalTravelMin = route.segments.reduce((s, seg) => s + seg.travel_time_min, 0);
    return trip.scheduled_departure_min + totalTravelMin;
  }

  function loadBadge(dr: DemandResult | undefined): string {
    if (!dr) return '—';
    const pct = Math.round(dr.capacity_utilization * 100);
    return `${dr.max_load}/${dr.capacity} (${pct}%)`;
  }

  function statusClass(dr: DemandResult | undefined, bus: Bus | undefined): string {
    if (bus?.status === 'unavailable') return 'badge--error';
    if (!dr) return 'badge--neutral';
    if (dr.total_unmet > 0) return 'badge--warn';
    if (dr.capacity_utilization >= 0.9) return 'badge--warn';
    return 'badge--ok';
  }

  function statusLabel(dr: DemandResult | undefined, bus: Bus | undefined): string {
    if (bus?.status === 'unavailable') return 'Bus unavailable';
    if (!dr) return 'No data';
    if (dr.total_unmet > 0) return `${dr.total_unmet} unmet`;
    return 'OK';
  }

  return (
    <section className="card" aria-label="Route and schedule table">
      <h2>Routes &amp; Schedule</h2>
      <div className="table-scroll">
        <table aria-label="Trip schedule with load and status">
          <thead>
            <tr>
              <th scope="col">Trip</th>
              <th scope="col">Route</th>
              <th scope="col">Bus</th>
              <th scope="col">Departure</th>
              <th scope="col">Est. End</th>
              <th scope="col">Load / Capacity</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {trips.map((trip) => {
              const route = routeMap[trip.route_id];
              const bus = busMap[trip.bus_id];
              const dr = demandMap[trip.trip_id];
              const sc = statusClass(dr, bus);
              return (
                <tr key={trip.trip_id} className={bus?.status === 'unavailable' ? 'row--unavailable' : ''}>
                  <td><strong>{trip.trip_id}</strong></td>
                  <td>{route ? `${route.route_id} — ${route.name}` : trip.route_id}</td>
                  <td>{trip.bus_id}{bus ? ` (${bus.capacity} seats)` : ''}</td>
                  <td>{minToHHMM(trip.scheduled_departure_min)}</td>
                  <td>{minToHHMM(tripEndMin(trip))}</td>
                  <td>{loadBadge(dr)}</td>
                  <td><span className={`badge ${sc}`}>{statusLabel(dr, bus)}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
