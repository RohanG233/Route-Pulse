import type { AlertItem } from '../types';

interface Props {
  alerts: AlertItem[];
}

const CODE_LABELS: Record<string, string> = {
  OVERLOAD: 'Overload',
  UNMET_DEMAND: 'Unmet demand',
  LATE_ARRIVAL: 'Late arrival',
  BUS_UNAVAILABLE: 'Bus unavailable',
  DATA_MISSING: 'Data missing',
};

export function AlertList({ alerts }: Props) {
  if (alerts.length === 0) {
    return (
      <section className="card" aria-label="Operational alerts">
        <h2>Alerts</h2>
        <p className="empty-state alert-ok">✓ No alerts for this scenario.</p>
      </section>
    );
  }

  return (
    <section className="card" aria-label="Operational alerts">
      <h2>Alerts <span className="badge badge--error">{alerts.length}</span></h2>
      <ul className="alert-list" role="list">
        {alerts.map((a, i) => (
          <li
            key={i}
            className={`alert-item alert-item--${a.severity}`}
            role="listitem"
          >
            <div className="alert-item__header">
              <span className={`badge badge--${a.severity === 'critical' ? 'error' : 'warn'}`}>
                {CODE_LABELS[a.code] ?? a.code}
              </span>
              {a.trip_id && <span className="alert-item__trip">{a.trip_id}</span>}
              {a.stop_id && <span className="alert-item__stop">@ {a.stop_id}</span>}
            </div>
            <p className="alert-item__msg">{a.message}</p>
            {a.detail && <p className="alert-item__detail">{a.detail}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
