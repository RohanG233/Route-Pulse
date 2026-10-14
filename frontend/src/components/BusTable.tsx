import type { Bus } from '../types';
import { minToHHMM } from '../types';

interface Props {
  buses: Bus[];
}

export function BusTable({ buses }: Props) {
  return (
    <section className="card" aria-label="Bus fleet">
      <h2>Buses</h2>
      <div className="table-scroll">
        <table aria-label="Bus fleet details">
          <thead>
            <tr>
              <th scope="col">Bus ID</th>
              <th scope="col">Capacity (seats)</th>
              <th scope="col">Available from</th>
              <th scope="col">Available until</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {buses.map((b) => (
              <tr key={b.bus_id} className={b.status === 'unavailable' ? 'row--unavailable' : ''}>
                <td><strong>{b.bus_id}</strong></td>
                <td>{b.capacity}</td>
                <td>{minToHHMM(b.available_from_min)}</td>
                <td>{minToHHMM(b.available_until_min)}</td>
                <td>
                  <span className={`badge badge--${b.status === 'available' ? 'ok' : 'error'}`}>
                    {b.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
