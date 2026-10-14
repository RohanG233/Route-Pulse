import type { EtaPredictionRow, Stop } from '../types';
import { minToHHMM } from '../types';

interface Props {
  /** keyed by trip_id */
  etaPredictions: Record<string, EtaPredictionRow[]>;
  stops: Stop[];
}

export function EtaTable({ etaPredictions, stops }: Props) {
  const stopMap = Object.fromEntries(stops.map((s) => [s.stop_id, s]));
  const tripIds = Object.keys(etaPredictions).sort();

  if (tripIds.length === 0) {
    return (
      <section className="card" aria-label="ETA predictions">
        <h2>ETA Predictions</h2>
        <p className="empty-state">No ETA data yet — select a scenario.</p>
      </section>
    );
  }

  return (
    <section className="card" aria-label="ETA predictions">
      <h2>ETA Predictions</h2>
      <div className="table-scroll">
        <table aria-label="Predicted arrivals per trip and stop">
          <thead>
            <tr>
              <th scope="col">Trip</th>
              <th scope="col">Stop</th>
              <th scope="col">Expected</th>
              <th scope="col">Window</th>
              <th scope="col">Source</th>
            </tr>
          </thead>
          <tbody>
            {tripIds.map((tripId) =>
              etaPredictions[tripId].map((pred, idx) => {
                const stop = stopMap[pred.stop_id];
                const isSource = pred.estimate_source === 'median_observed';
                const windowStr = `${minToHHMM(pred.window_start_min)} – ${minToHHMM(pred.window_end_min)}`;
                return (
                  <tr key={`${tripId}-${pred.stop_id}`}>
                    {idx === 0 && (
                      <td rowSpan={etaPredictions[tripId].length} className="td--trip">
                        <strong>{tripId}</strong>
                      </td>
                    )}
                    <td title={stop?.name ?? pred.stop_id}>
                      {stop ? `${pred.stop_id} — ${stop.name}` : pred.stop_id}
                    </td>
                    <td>
                      {/* baseline endpoint provides hhmm, fall back to computed */}
                      {pred.expected_arrival_hhmm ?? minToHHMM(Math.round(pred.expected_arrival_min))}
                    </td>
                    <td className="td--window">{windowStr}</td>
                    <td>
                      <span
                        className={`badge ${isSource ? 'badge--ok' : 'badge--neutral'}`}
                        title={pred.assumptions}
                      >
                        {isSource ? 'GPS' : 'fallback'}
                      </span>
                    </td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
