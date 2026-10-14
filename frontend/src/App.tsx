/**
 * RoutePulse operator dashboard.
 *
 * Workflow:
 *   1. On mount: fetch health, network, scenarios, and baseline for the
 *      default scenario ("normal").
 *   2. Selecting a scenario: fetch its baseline (ETA + demand + alerts).
 *   3. "Run Planning" button: POST /api/plan → show recommendation,
 *      comparison panel, updated alerts.
 *   4. Reset: clear plan result, reload baseline.
 *
 * All metrics are computed from API responses — never hardcoded.
 * API errors are displayed inline; the UI never silently swallows them.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchBaseline,
  fetchHealth,
  fetchNetwork,
  fetchScenarios,
  postPlan,
} from './api/client'
import type {
  BaselineResponse,
  NetworkResponse,
  PlanResponse,
  ScenarioSummary,
} from './types'
import { minToHHMM } from './types'
import { AlertList } from './components/AlertList'
import { BusTable } from './components/BusTable'
import { ComparisonPanel } from './components/ComparisonPanel'
import { EtaTable } from './components/EtaTable'
import { MetricCard } from './components/MetricCard'
import { RecommendationPanel } from './components/RecommendationPanel'
import { RouteTable } from './components/RouteTable'
import { ScenarioSelector } from './components/ScenarioSelector'

// ---------------------------------------------------------------------------
// App state types
// ---------------------------------------------------------------------------

type ApiStatus = 'checking' | 'ok' | 'error'

// ---------------------------------------------------------------------------
// Helper: generic error display
// ---------------------------------------------------------------------------

function ErrorBanner({ msg, onRetry }: { msg: string; onRetry?: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <span>⚠ {msg}</span>
      {onRetry && (
        <button className="btn btn--small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------

export default function App() {
  // ── API health ─────────────────────────────────────────────────────────
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')

  // ── Network (static, loaded once) ──────────────────────────────────────
  const [network, setNetwork] = useState<NetworkResponse | null>(null)
  const [networkError, setNetworkError] = useState<string | null>(null)

  // ── Scenarios ──────────────────────────────────────────────────────────
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [selectedScenario, setSelectedScenario] = useState<string>('normal')

  // ── Baseline (loaded per scenario) ─────────────────────────────────────
  const [baseline, setBaseline] = useState<BaselineResponse | null>(null)
  const [baselineLoading, setBaselineLoading] = useState(false)
  const [baselineError, setBaselineError] = useState<string | null>(null)

  // ── Plan result ────────────────────────────────────────────────────────
  const [planResult, setPlanResult] = useState<PlanResponse | null>(null)
  const [planLoading, setPlanLoading] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)

  // Abort controller for in-flight baseline requests
  const abortRef = useRef<AbortController | null>(null)

  // ── On mount: health + network + scenarios ──────────────────────────────

  useEffect(() => {
    async function init() {
      // Health check
      try {
        await fetchHealth()
        setApiStatus('ok')
      } catch {
        setApiStatus('error')
        return
      }

      // Network + scenarios in parallel
      try {
        const [net, scens] = await Promise.all([
          fetchNetwork(),
          fetchScenarios(),
        ])
        setNetwork(net)
        setScenarios(scens)
      } catch (err) {
        setNetworkError(err instanceof Error ? err.message : String(err))
      }
    }
    init()
  }, [])

  // ── Load baseline whenever scenario changes ─────────────────────────────

  const loadBaseline = useCallback(async (scenarioId: string) => {
    // Cancel any in-flight baseline request
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    setBaselineLoading(true)
    setBaselineError(null)
    setBaseline(null)
    setPlanResult(null)
    setPlanError(null)

    try {
      const data = await fetchBaseline(scenarioId)
      setBaseline(data)
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setBaselineError(err.message)
      }
    } finally {
      setBaselineLoading(false)
    }
  }, [])

  // Load initial baseline once network is ready
  useEffect(() => {
    if (apiStatus === 'ok') {
      loadBaseline(selectedScenario)
    }
  }, [apiStatus]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleScenarioChange = (id: string) => {
    setSelectedScenario(id)
    loadBaseline(id)
  }

  // ── Run planning ────────────────────────────────────────────────────────

  const handleRunPlanning = async () => {
    setPlanLoading(true)
    setPlanError(null)
    setPlanResult(null)

    try {
      const result = await postPlan({ scenario_id: selectedScenario })
      setPlanResult(result)
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err))
    } finally {
      setPlanLoading(false)
    }
  }

  const handleReset = () => {
    setPlanResult(null)
    setPlanError(null)
    loadBaseline(selectedScenario)
  }

  // ── Derived display data ────────────────────────────────────────────────

  const alerts = baseline?.alerts ?? []
  const etaPredictions = baseline?.eta_predictions ?? {}
  const demandResults = baseline?.demand_results ?? []

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="app">
      {/* ── Header ── */}
      <header className="app-header">
        <div className="header-left">
          <h1 className="app-title">RoutePulse</h1>
          <span className="app-subtitle">Campus Transport Planning</span>
        </div>
        <div className="header-right">
          <span
            className={`api-status badge ${
              apiStatus === 'ok'
                ? 'badge--ok'
                : apiStatus === 'error'
                ? 'badge--error'
                : 'badge--neutral'
            }`}
            aria-live="polite"
          >
            API {apiStatus === 'checking' ? '…' : apiStatus === 'ok' ? '✓ online' : '✗ offline'}
          </span>
          {network && (
            <span className="dataset-info">
              Seed {network.dataset_seed} · v{network.dataset_version}
            </span>
          )}
        </div>
      </header>

      {/* ── API offline banner ── */}
      {apiStatus === 'error' && (
        <ErrorBanner
          msg="Cannot reach the backend. Start it with: cd backend && uvicorn app.main:app --reload --port 8000"
          onRetry={() => window.location.reload()}
        />
      )}

      {apiStatus === 'ok' && (
        <main className="app-main">
          {/* ── Network error ── */}
          {networkError && (
            <ErrorBanner msg={networkError} onRetry={() => window.location.reload()} />
          )}

          {/* ── Scenario selector ── */}
          {scenarios.length > 0 && (
            <ScenarioSelector
              scenarios={scenarios}
              selected={selectedScenario}
              onChange={handleScenarioChange}
              disabled={baselineLoading || planLoading}
            />
          )}

          {/* ── Network overview cards ── */}
          {network && (
            <section className="card" aria-label="Network overview">
              <h2>Network Overview</h2>
              <div className="metric-grid">
                <MetricCard label="Buses" value={network.buses.length} unit="vehicles" />
                <MetricCard label="Routes" value={network.routes.length} />
                <MetricCard label="Stops" value={network.stops.length} />
                <MetricCard label="Trips" value={network.trips.length} unit="scheduled" />
                {network.buses.map((b) => (
                  <MetricCard
                    key={b.bus_id}
                    label={b.bus_id}
                    value={b.capacity}
                    unit="seats"
                    highlight={b.status === 'available' ? 'good' : 'bad'}
                    sub={`${minToHHMM(b.available_from_min)}–${minToHHMM(b.available_until_min)}`}
                  />
                ))}
              </div>
            </section>
          )}

          {/* ── Baseline loading / error ── */}
          {baselineLoading && (
            <div className="loading-banner" aria-live="polite">
              Loading scenario data…
            </div>
          )}
          {baselineError && (
            <ErrorBanner
              msg={baselineError}
              onRetry={() => loadBaseline(selectedScenario)}
            />
          )}

          {/* ── Route / schedule table ── */}
          {network && (
            <RouteTable
              trips={network.trips}
              routes={network.routes}
              buses={network.buses}
              demandResults={demandResults}
            />
          )}

          {/* ── ETA table ── */}
          {network && (
            <EtaTable etaPredictions={etaPredictions} stops={network.stops} />
          )}

          {/* ── Alerts ── */}
          <AlertList alerts={alerts} />

          {/* ── Run Planning / Reset buttons ── */}
          <section className="card planning-controls" aria-label="Planning controls">
            <h2>Planning</h2>

            {/* Reproducibility note */}
            <p className="repro-note">
              Scenario: <strong>{selectedScenario}</strong> · Seed: {network?.dataset_seed ?? '…'} ·
              Data: deterministic (re-run resets to same results)
            </p>

            <div className="btn-row">
              <button
                className="btn btn--primary"
                onClick={handleRunPlanning}
                disabled={planLoading || baselineLoading || apiStatus !== 'ok'}
                aria-busy={planLoading}
              >
                {planLoading ? 'Running…' : 'Run Planning / Optimise'}
              </button>
              {planResult && (
                <button className="btn btn--secondary" onClick={handleReset}>
                  Reset
                </button>
              )}
            </div>

            {planError && <ErrorBanner msg={planError} />}
          </section>

          {/* ── Recommendation panel (shown after planning) ── */}
          {planResult && <RecommendationPanel plan={planResult} />}

          {/* ── Comparison panel (shown after planning) ── */}
          {planResult && <ComparisonPanel plan={planResult} />}

          {/* ── Bus table ── */}
          {network && <BusTable buses={network.buses} />}
        </main>
      )}

      <footer className="app-footer">
        <p>RoutePulse MVP · Synthetic campus data · Not for operational use</p>
      </footer>
    </div>
  )
}
