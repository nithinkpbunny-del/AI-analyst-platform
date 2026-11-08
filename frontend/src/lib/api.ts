export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export interface Health {
  status: 'ok'
  service: string
  version: string
  env: string
}

export interface Readiness {
  status: 'ok' | 'unavailable'
  checks: Record<string, 'ok' | 'failed'>
}

/** GET a JSON document. A 503 from /health/ready still carries a useful body, so only network errors throw. */
async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { signal })
  return (await response.json()) as T
}

export function getHealth(signal?: AbortSignal): Promise<Health> {
  return getJson<Health>('/health', signal)
}

export function getReadiness(signal?: AbortSignal): Promise<Readiness> {
  return getJson<Readiness>('/health/ready', signal)
}
