/**
 * RoutePulse TypeScript types.
 * Derived from backend/app/schemas.py and the API contracts documented in
 * docs/assumptions.md. All *_min fields are minutes from midnight (integer).
 */

// ---------------------------------------------------------------------------
// Dataset entities
// ---------------------------------------------------------------------------

export interface Bus {
  bus_id: string;
  capacity: number;
  available_from_min: number;
  available_until_min: number;
  status: string; // "available" | "unavailable"
}

export interface Stop {
  stop_id: string;
  name: string;
  latitude: number;
  longitude: number;
}

export interface RouteSegment {
  from_stop_id: string;
  to_stop_id: string;
  travel_time_min: number;
}

export interface Route {
  route_id: string;
  name: string;
  stop_ids: string[];
  segments: RouteSegment[];
}

export interface Trip {
  trip_id: string;
  route_id: string;
  bus_id: string;
  scheduled_departure_min: number;
  permitted_departure_window_min: number;
  service_date: string;
}

// ---------------------------------------------------------------------------
// Network response
// ---------------------------------------------------------------------------

export interface NetworkResponse {
  buses: Bus[];
  stops: Stop[];
  routes: Route[];
  trips: Trip[];
  dataset_seed: number;
  dataset_version: string;
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

export interface ScenarioSummary {
  scenario_id: string;
  name: string;
  description: string;
}

// ---------------------------------------------------------------------------
// Alerts (from baseline endpoint — Alert.to_dict() shape)
// ---------------------------------------------------------------------------

export type AlertCode =
  | 'OVERLOAD'
  | 'UNMET_DEMAND'
  | 'LATE_ARRIVAL'
  | 'BUS_UNAVAILABLE'
  | 'DATA_MISSING';

export type AlertSeverity = 'warning' | 'critical';

export interface AlertItem {
  code: AlertCode;
  severity: AlertSeverity;
  trip_id: string | null;
  stop_id: string | null;
  message: string;
  detail: string;
}

// ---------------------------------------------------------------------------
// Constraint validation
// ---------------------------------------------------------------------------

export interface ConstraintViolation {
  code: string; // "C1" – "C7"
  message: string;
  trip_id: string | null;
  bus_id: string | null;
  stop_id: string | null;
}

export interface ValidationResult {
  passed: boolean;
  violations: ConstraintViolation[];
}

// ---------------------------------------------------------------------------
// Demand / load simulation
// ---------------------------------------------------------------------------

export interface StopLoadEvent {
  trip_id: string;
  stop_id: string;
  boarded: number;
  alighted: number;
  onboard_after: number;
  capacity: number;
  overloaded: boolean;
  unmet_at_stop: number;
}

export interface DemandResult {
  trip_id: string;
  bus_id: string;
  capacity: number;
  stop_events: StopLoadEvent[];
  total_served: number;
  total_unmet: number;
  max_load: number;
  capacity_utilization: number; // 0.0–1.0 ratio
}

// ---------------------------------------------------------------------------
// ETA predictions (baseline endpoint adds expected_arrival_hhmm)
// ---------------------------------------------------------------------------

export interface EtaPredictionRow {
  stop_id: string;
  expected_arrival_min: number;
  expected_arrival_hhmm: string; // "HH:MM" — only on baseline endpoint
  window_start_min: number;
  window_end_min: number;
  estimate_source: 'median_observed' | 'fallback_configured';
  assumptions: string;
}

// baseline endpoint response
export interface BaselineResponse {
  scenario_id: string;
  scenario_name: string;
  validation: ValidationResult;
  alerts: AlertItem[];
  demand_results: DemandResult[];
  eta_predictions: Record<string, EtaPredictionRow[]>; // keyed by trip_id
}

// ---------------------------------------------------------------------------
// Planning  — POST /api/plan response
// ---------------------------------------------------------------------------

export interface MetricSnapshot {
  avg_waiting_time_min: number;
  unmet_passengers: number;
  capacity_violations: number;
  fleet_utilization_pct: number;
  eta_mae_min: number | null;
  eta_window_coverage_pct: number | null;
  missed_connections: string; // count or "not_modelled"
  objective_cost: number;
}

export interface MetricDelta {
  metric: string;
  baseline: number | string | null;
  proposed: number | string | null;
  absolute_delta: number | null;
  pct_change: number | null; // null when baseline is zero or non-numeric
}

export interface RecommendedAction {
  action_type: 'reassign_bus' | 'shift_departure';
  trip_id: string;
  original_bus_id: string | null;
  new_bus_id: string | null;
  original_departure_min: number | null;
  new_departure_min: number | null;
  reason: string;
}

export interface EtaQualityMetrics {
  mae_minutes: number;
  evaluated_count: number;
  window_coverage_pct: number;
}

export type RecommendationStatus =
  | 'improvement_found'
  | 'no_improvement'
  | 'infeasible_baseline';

export interface PlanResponse {
  scenario_id: string;
  recommendation_status: RecommendationStatus;
  recommendation_summary: string;
  actions: RecommendedAction[];
  constraint_validation: ValidationResult;
  baseline_metrics: MetricSnapshot;
  proposed_metrics: MetricSnapshot | null;
  metric_deltas: MetricDelta[];
  eta_quality: EtaQualityMetrics | null;
  assumptions: string[];
  limitations: string[];
}

export interface PlanRequest {
  scenario_id: string;
  objective_weights?: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Helper: minutes-from-midnight → "HH:MM"
// ---------------------------------------------------------------------------

export function minToHHMM(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Helper: format a metric value for display
// ---------------------------------------------------------------------------

export function fmtNumber(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined) return '—';
  return v.toFixed(decimals);
}

export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'N/A';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}
