/**
 * RoutePulse API client.
 *
 * All functions call the FastAPI backend at BASE_URL.
 * CORS is already configured on the backend for localhost:5173.
 * Every function throws an Error with a user-readable message on non-2xx.
 */

import type {
  BaselineResponse,
  NetworkResponse,
  PlanRequest,
  PlanResponse,
  ScenarioSummary,
} from '../types';

const BASE_URL = (import.meta.env.VITE_API_URL as string | undefined)
  ?? 'http://localhost:8000/api';

// ---------------------------------------------------------------------------
// Internal fetch wrapper
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch (err) {
    throw new Error(
      'Cannot reach the RoutePulse backend. Is the server running?\n' +
        'Start it with: cd backend && uvicorn app.main:app --reload --port 8000',
    );
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body?.detail ?? detail;
    } catch {
      // ignore JSON parse error
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Endpoint wrappers
// ---------------------------------------------------------------------------

/** GET /api/health — liveness check. */
export async function fetchHealth(): Promise<{ status: string }> {
  return apiFetch('/health');
}

/** GET /api/network — buses, stops, routes, trips, seed. */
export async function fetchNetwork(): Promise<NetworkResponse> {
  return apiFetch('/network');
}

/** GET /api/scenarios — list all scenario summaries. */
export async function fetchScenarios(): Promise<ScenarioSummary[]> {
  return apiFetch('/scenarios');
}

/**
 * GET /api/scenarios/{scenario_id}/baseline
 * Returns ETA predictions, demand results, alerts, and validation
 * without running the planner.
 */
export async function fetchBaseline(
  scenarioId: string,
): Promise<BaselineResponse> {
  return apiFetch(`/scenarios/${encodeURIComponent(scenarioId)}/baseline`);
}

/**
 * POST /api/plan
 * Runs the full planning cycle: baseline + candidate search + comparison.
 */
export async function postPlan(req: PlanRequest): Promise<PlanResponse> {
  return apiFetch('/plan', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

/**
 * GET /api/scenarios/{scenario_id}/comparison
 * Deterministic re-computation of the plan comparison (no POST required).
 */
export async function fetchComparison(
  scenarioId: string,
): Promise<PlanResponse> {
  return apiFetch(
    `/scenarios/${encodeURIComponent(scenarioId)}/comparison`,
  );
}
