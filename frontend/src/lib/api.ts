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

// ------------------------------------------------------------------------------------------------ API calls

/** Every API error has `{ error: { code, message, fields? } }`; `code` is stable, `message` is for people. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fields: Record<string, string>

  constructor(status: number, code: string, message: string, fields: Record<string, string> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fields = fields
  }
}

export const NETWORK_ERROR_CODE = 'network_error'

interface ErrorBody {
  error?: { code?: string; message?: string; fields?: Record<string, string> }
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: ErrorBody = {}
  try {
    body = (await response.json()) as ErrorBody
  } catch {
    /* not JSON */
  }
  return new ApiError(
    response.status,
    body.error?.code ?? 'http_error',
    body.error?.message ?? 'Something went wrong. Try again.',
    body.error?.fields ?? {},
  )
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
}

/** Low-level request: JSON in and out, cookies included (refresh + CSRF cookies live on the API host). */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: 'include',
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(
      0,
      NETWORK_ERROR_CODE,
      'The server could not be reached. Check your connection and try again.',
    )
  }
  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

// ------------------------------------------------------------------------------------------- auth plumbing
// The access token lives in memory only (never localStorage), so a page reload asks /auth/refresh for a new one
// using the HttpOnly refresh cookie.

let accessToken: string | null = null
let refreshHandler: (() => Promise<string | null>) | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

/** The auth provider registers how to get a fresh access token (one shared refresh, see lib/auth). */
export function setRefreshHandler(handler: (() => Promise<string | null>) | null) {
  refreshHandler = handler
}

/** An authenticated request. On `token_expired` it refreshes once and retries. */
export async function authedRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const attempt = (token: string | null) =>
    request<T>(path, {
      ...options,
      headers: { ...options.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    })
  try {
    return await attempt(accessToken)
  } catch (error) {
    const expired =
      error instanceof ApiError &&
      error.status === 401 &&
      (error.code === 'token_expired' || !accessToken)
    if (!expired || !refreshHandler) throw error
    const fresh = await refreshHandler()
    if (!fresh) throw error
    return attempt(fresh)
  }
}
