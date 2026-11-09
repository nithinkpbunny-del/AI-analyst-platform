import { vi } from 'vitest'
import { demoSession } from '../lib/demoSession'
import type { SessionResponse } from '../lib/authApi'

type Reply =
  | { status: number; body?: unknown }
  | ((init: RequestInit, url: URL) => { status: number; body?: unknown })

/**
 * A fake API for component tests: map "METHOD /path" to a reply. Unknown routes answer 404. Every call is
 * recorded so tests can check what was sent.
 */
export function fakeApi(routes: Record<string, Reply>) {
  const calls: { key: string; init: RequestInit; url: string }[] = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input))
    const key = `${init.method ?? 'GET'} ${url.pathname}`
    calls.push({ key, init, url: url.href })
    const route = routes[key]
    const reply = typeof route === 'function' ? route(init, url) : route
    const { status, body } = reply ?? {
      status: 404,
      body: { error: { code: 'not_found', message: 'Not found.' } },
    }
    return Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    calls,
    count: (key: string) => calls.filter((call) => call.key === key).length,
    body: (key: string) => {
      const call = [...calls].reverse().find((c) => c.key === key)
      return call?.init.body ? (JSON.parse(String(call.init.body)) as unknown) : undefined
    },
    header: (key: string, name: string) => {
      const call = [...calls].reverse().find((c) => c.key === key)
      return (call?.init.headers as Record<string, string> | undefined)?.[name]
    },
  }
}

export function apiError(
  status: number,
  code: string,
  message: string,
  fields?: Record<string, string>,
) {
  return { status, body: { error: { code, message, ...(fields ? { fields } : {}) } } }
}

export const csrfRoute = { 'GET /auth/csrf': { status: 200, body: { csrf_token: 'csrf-1' } } }

export const signedOutRoutes = {
  ...csrfRoute,
  'POST /auth/refresh': apiError(401, 'not_signed_in', 'Sign in to continue.'),
}

export function sessionResponse(overrides: Partial<SessionResponse> = {}): SessionResponse {
  return {
    ...demoSession,
    status: 'signed_in',
    access_token: 'access-1',
    token_type: 'bearer',
    expires_in: 600,
    csrf_token: 'csrf-1',
    ...overrides,
  }
}
