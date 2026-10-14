import type { PlanResponse, MetricSnapshot, MetricDelta } from '../types';
import { fmtNumber, fmtPct } from '../types';

interface Props {
  plan: PlanResponse;
}

// Human-readable labels and units for each metric key
const METRIC_META: Record<string, { label: string; unit: string; lowerIsBetter: boolean }> = {
  avg_waiting_time_min:     { label: 'Avg waiting time',      unit: 'min',    lowerIsBetter: true },
  unmet_passengers:         { label: 'Unmet passengers',      unit: 'pax',    lowerIsBetter: true },
  capacity_violations:      { label: 'Capacity violations',   unit: 'events', lowerIsBetter: true },
  fleet_utilization_pct:    { label: 'Fleet utilisation',     unit: '%',      lowerIsBetter: false },
  objective_cost:           { label: 'Objective cost',        unit: '',       lowerIsBetter: true },
  eta_mae_min:              { label: 'ETA MAE',               unit: 'min',    lowerIsBetter: true },
};

function formatVal(key: string, v: number | string | null): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  const meta = METRIC_META[key];
  if (!meta) return fmtNumber(v);
  return `${fmtNumber(v)}${meta.unit ? ' ' + meta.unit : ''}`;
}

function deltaClass(key: string, pct: number | null): string {
  if (pct === null) return '';
  const meta = METRIC_META[key];
  if (!meta) return '';
  const better = meta.lowerIsBetter ? pct > 0 : pct < 0;
  return better ? 'delta--good' : pct === 0 ? '' : 'delta--bad';
}

function SnapshotColumn({
  label,
  snapshot,
}: {
  label: string;
  snapshot: MetricSnapshot;
}) {
  return (
    <div className="snapshot-col">
      <h4>{label}</h4>
      <table className="snapshot-table" aria-label={`${label} metrics`}>
        <tbody>
          {Object.entries(METRIC_META).map(([key, meta]) => {
            const val = snapshot[key as keyof MetricSnapshot];
            return (
              <tr key={key}>
                <td>{meta.label}</td>
                <td>{formatVal(key, val as number | string | null)}</td>
              </tr>
            );
          })}
          <tr>
            <td>Missed connections</td>
            <td>{snapshot.missed_connections}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function ComparisonPanel({ plan }: Props) {
  const hasProposed = plan.proposed_metrics !== null;

  return (
    <section className="card comparison-panel" aria-label="Baseline vs proposed comparison">
      <h2>Baseline vs Proposed</h2>

      {/* ETA quality row */}
      {plan.eta_quality && (
        <div className="eta-quality-row">
          <span>ETA MAE: <strong>{fmtNumber(plan.eta_quality.mae_minutes)} min</strong></span>
          <span>Window coverage: <strong>{fmtNumber(plan.eta_quality.window_coverage_pct, 1)}%</strong></span>
          <span className="eta-quality-count">({plan.eta_quality.evaluated_count} stops evaluated)</span>
        </div>
      )}

      <div className="comparison-cols">
        <SnapshotColumn label="Baseline" snapshot={plan.baseline_metrics} />
        {hasProposed && plan.proposed_metrics ? (
          <SnapshotColumn label="Proposed" snapshot={plan.proposed_metrics} />
        ) : (
          <div className="snapshot-col snapshot-col--empty">
            <h4>Proposed</h4>
            <p className="empty-state">
              {plan.recommendation_status === 'no_improvement'
                ? 'No improvement found'
                : plan.recommendation_status === 'infeasible_baseline'
                ? 'Baseline infeasible — see recommendation'
                : '—'}
            </p>
          </div>
        )}

        {/* Delta column */}
        {hasProposed && plan.metric_deltas.length > 0 && (
          <div className="snapshot-col snapshot-col--delta">
            <h4>Change</h4>
            <table className="snapshot-table" aria-label="Metric changes">
              <tbody>
                {plan.metric_deltas.map((d) => {
                  const cls = deltaClass(d.metric, d.pct_change);
                  const meta = METRIC_META[d.metric];
                  if (!meta) return null;
                  return (
                    <tr key={d.metric}>
                      <td>{meta.label}</td>
                      <td className={cls}>
                        {d.pct_change !== null
                          ? fmtPct(d.pct_change)
                          : d.absolute_delta !== null
                          ? (d.absolute_delta >= 0 ? '+' : '') + fmtNumber(d.absolute_delta)
                          : 'N/A'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Constraint badge for proposed */}
      {hasProposed && (
        <div className="comparison-constraint-badge">
          <span
            className={`badge ${plan.constraint_validation.passed ? 'badge--ok' : 'badge--error'}`}
          >
            Proposed plan constraints:{' '}
            {plan.constraint_validation.passed
              ? '✓ All passed (C1–C7)'
              : `✗ ${plan.constraint_validation.violations.length} violation(s)`}
          </span>
        </div>
      )}
    </section>
  );
}
