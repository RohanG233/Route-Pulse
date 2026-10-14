import type { PlanResponse, RecommendedAction } from '../types';
import { minToHHMM } from '../types';

interface Props {
  plan: PlanResponse;
}

function ActionDetail({ action }: { action: RecommendedAction }) {
  if (action.action_type === 'reassign_bus') {
    return (
      <div className="action-detail">
        <strong>Reassign bus</strong> on trip <strong>{action.trip_id}</strong>
        <br />
        {action.original_bus_id} → <strong>{action.new_bus_id}</strong>
        <p className="action-reason">{action.reason}</p>
      </div>
    );
  }
  return (
    <div className="action-detail">
      <strong>Shift departure</strong> on trip <strong>{action.trip_id}</strong>
      <br />
      {action.original_departure_min !== null && action.new_departure_min !== null && (
        <>
          {minToHHMM(action.original_departure_min)} →{' '}
          <strong>{minToHHMM(action.new_departure_min)}</strong>
          {' '}
          ({action.new_departure_min - action.original_departure_min > 0 ? '+' : ''}
          {action.new_departure_min - action.original_departure_min} min)
        </>
      )}
      <p className="action-reason">{action.reason}</p>
    </div>
  );
}

const STATUS_CONFIG = {
  improvement_found: { cls: 'status--good', icon: '✓', label: 'Improvement found' },
  no_improvement: { cls: 'status--neutral', icon: '–', label: 'No improvement found' },
  infeasible_baseline: { cls: 'status--bad', icon: '⚠', label: 'Baseline infeasible' },
} as const;

export function RecommendationPanel({ plan }: Props) {
  const cfg = STATUS_CONFIG[plan.recommendation_status];
  const cv = plan.constraint_validation;

  return (
    <section className="card recommendation-panel" aria-label="Planning recommendation">
      <h2>Recommendation</h2>

      <div className={`rec-status ${cfg.cls}`}>
        <span className="rec-status__icon" aria-hidden="true">{cfg.icon}</span>
        <span className="rec-status__label">{cfg.label}</span>
        <span
          className={`badge ${cv.passed ? 'badge--ok' : 'badge--error'}`}
          title={cv.passed ? 'All constraints satisfied' : `${cv.violations.length} constraint violation(s)`}
        >
          Constraints: {cv.passed ? '✓ passed' : `✗ ${cv.violations.length} violation(s)`}
        </span>
      </div>

      <p className="rec-summary">{plan.recommendation_summary}</p>

      {plan.actions.length > 0 && (
        <div className="rec-actions">
          <h3>Actions</h3>
          {plan.actions.map((a, i) => (
            <ActionDetail key={i} action={a} />
          ))}
        </div>
      )}

      {!cv.passed && cv.violations.length > 0 && (
        <details className="rec-violations">
          <summary>Constraint violations ({cv.violations.length})</summary>
          <ul>
            {cv.violations.map((v, i) => (
              <li key={i}>
                <strong>{v.code}</strong>{v.trip_id ? ` [${v.trip_id}]` : ''}: {v.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      {plan.assumptions.length > 0 && (
        <details className="rec-assumptions">
          <summary>Assumptions &amp; limitations</summary>
          <ul>
            {plan.assumptions.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
          <ul>
            {plan.limitations.map((l, i) => <li key={i} className="limitation">{l}</li>)}
          </ul>
        </details>
      )}
    </section>
  );
}
