interface Props {
  label: string;
  value: string | number;
  unit?: string;
  highlight?: 'good' | 'bad' | 'neutral';
  sub?: string;
}

/**
 * A single metric tile used in the network overview and metric snapshots.
 */
export function MetricCard({ label, value, unit, highlight = 'neutral', sub }: Props) {
  return (
    <div className={`metric-card metric-card--${highlight}`} role="status">
      <span className="metric-card__label">{label}</span>
      <span className="metric-card__value">
        {value}
        {unit && <span className="metric-card__unit"> {unit}</span>}
      </span>
      {sub && <span className="metric-card__sub">{sub}</span>}
    </div>
  );
}
