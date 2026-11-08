import { useEffect, useState } from 'react'
import { API_BASE_URL, getHealth, getReadiness, type Health, type Readiness } from './lib/api'

type ApiState =
  | { kind: 'loading' }
  | { kind: 'unreachable' }
  | { kind: 'connected'; health: Health; readiness: Readiness }

export default function App() {
  const [state, setState] = useState<ApiState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([getHealth(controller.signal), getReadiness(controller.signal)])
      .then(([health, readiness]) => setState({ kind: 'connected', health, readiness }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ kind: 'unreachable' })
      })
    return () => controller.abort()
  }, [])

  return (
    <main>
      <h1>AI Business Analyst</h1>
      <p>Foundation build (milestone 1). The product screens arrive in the next milestones.</p>

      <h2>System status</h2>
      {state.kind === 'loading' && <p role="status">Checking the API…</p>}
      {state.kind === 'unreachable' && (
        <p role="alert">
          The API at {API_BASE_URL} is not reachable. Start it with <code>make dev</code>.
        </p>
      )}
      {state.kind === 'connected' && (
        <>
          <p role="status">
            API connected: version {state.health.version}, {state.health.env} environment.
          </p>
          <ul aria-label="Service checks">
            {Object.entries(state.readiness.checks).map(([name, result]) => (
              <li key={name}>
                {name}: {result === 'ok' ? 'ready' : 'not ready'}
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  )
}
