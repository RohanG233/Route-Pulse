import type { ScenarioSummary } from '../types';

interface Props {
  scenarios: ScenarioSummary[];
  selected: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}

export function ScenarioSelector({ scenarios, selected, onChange, disabled }: Props) {
  return (
    <section className="card scenario-selector" aria-label="Scenario selector">
      <h2>Scenario</h2>
      <div className="scenario-buttons" role="group" aria-label="Available scenarios">
        {scenarios.map((s) => (
          <button
            key={s.scenario_id}
            className={`scenario-btn${selected === s.scenario_id ? ' active' : ''}`}
            onClick={() => onChange(s.scenario_id)}
            disabled={disabled}
            aria-pressed={selected === s.scenario_id}
            title={s.description}
          >
            {s.name}
          </button>
        ))}
      </div>
      {scenarios.find((s) => s.scenario_id === selected) && (
        <p className="scenario-desc">
          {scenarios.find((s) => s.scenario_id === selected)!.description}
        </p>
      )}
    </section>
  );
}
