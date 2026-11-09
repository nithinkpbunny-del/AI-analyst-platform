import { describe, expect, it } from 'vitest'
import { apiError, fakeApi, sessionResponse } from '../testing/fakeApi'
import { ApiError, authedRequest, request, setAccessToken, setRefreshHandler } from './api'
import { activityDecision, endedReason, refreshSession } from './authApi'

describe('request', () => {
  it('turns the API error shape into an ApiError', async () => {
    fakeApi({
      'POST /auth/login': apiError(401, 'invalid_credentials', 'Invalid email or password.'),
    })
    const error = await request('/auth/login', { method: 'POST', body: {} }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 401, code: 'invalid_credentials' })
  })

  it('sends cookies and JSON', async () => {
    const api = fakeApi({ 'POST /x': { status: 200, body: { ok: true } } })
    await request('/x', { method: 'POST', body: { a: 1 } })
    expect(api.calls[0]?.init.credentials).toBe('include')
    expect(api.body('POST /x')).toEqual({ a: 1 })
  })

  it('reports a network failure in plain language', async () => {
    const { vi } = await import('vitest')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(request('/x')).rejects.toMatchObject({ code: 'network_error' })
  })

  it('handles 204 No Content', async () => {
    fakeApi({ 'POST /auth/logout': { status: 204 } })
    await expect(request('/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
  })
})

describe('authedRequest', () => {
  it('adds the bearer token', async () => {
    const api = fakeApi({ 'GET /auth/me': { status: 200, body: {} } })
    setAccessToken('token-a')
    await authedRequest('/auth/me')
    expect(api.header('GET /auth/me', 'Authorization')).toBe('Bearer token-a')
  })

  it('refreshes once on token_expired and retries with the new token', async () => {
    let first = true
    const api = fakeApi({
      'GET /auth/me': (init) => {
        const auth = (init.headers as Record<string, string>).Authorization
        if (first && auth === 'Bearer old') {
          first = false
          return apiError(401, 'token_expired', 'Your sign-in needs refreshing.')
        }
        return { status: 200, body: { auth } }
      },
    })
    setAccessToken('old')
    setRefreshHandler(() => Promise.resolve('new'))
    await expect(authedRequest('/auth/me')).resolves.toEqual({ auth: 'Bearer new' })
    expect(api.count('GET /auth/me')).toBe(2)
    setRefreshHandler(null)
  })

  it('does not refresh for other errors', async () => {
    fakeApi({ 'GET /auth/me': apiError(401, 'session_idle', 'Signed out.') })
    setAccessToken('t')
    let refreshed = false
    setRefreshHandler(() => {
      refreshed = true
      return Promise.resolve('new')
    })
    await expect(authedRequest('/auth/me')).rejects.toMatchObject({ code: 'session_idle' })
    expect(refreshed).toBe(false)
    setRefreshHandler(null)
  })
})

describe('refreshSession', () => {
  it('shares one refresh between parallel callers (rotation must never race)', async () => {
    const api = fakeApi({
      'GET /auth/csrf': { status: 200, body: { csrf_token: 'c' } },
      'POST /auth/refresh': { status: 200, body: sessionResponse() },
    })
    const [a, b] = await Promise.all([refreshSession(), refreshSession()])
    expect(a).toBe(b)
    expect(api.count('POST /auth/refresh')).toBe(1)
    expect(api.header('POST /auth/refresh', 'X-CSRF-Token')).toBe('c')
    await refreshSession()
    expect(api.count('POST /auth/refresh')).toBe(2)
  })

  it('explains endings worth explaining, and nothing else', () => {
    expect(endedReason(new ApiError(401, 'session_idle', 'Idle.'))).toBe('Idle.')
    expect(endedReason(new ApiError(401, 'not_signed_in', 'Sign in.'))).toBeNull()
    expect(endedReason(new Error('x'))).toBeNull()
  })
})

describe('activityDecision', () => {
  const idleMs = 30 * 60_000
  it('signs out after the idle time without interaction', () => {
    expect(activityDecision({ now: idleMs, lastActivity: 0, lastPing: 0, idleMs })).toBe('sign-out')
  })
  it('pings the server while the person is active', () => {
    expect(
      activityDecision({ now: 5 * 60_000, lastActivity: 4 * 60_000, lastPing: 0, idleMs }),
    ).toBe('ping')
  })
  it('does nothing when nothing happened since the last ping', () => {
    expect(activityDecision({ now: 10 * 60_000, lastActivity: 1000, lastPing: 2000, idleMs })).toBe(
      'none',
    )
    expect(activityDecision({ now: 60_000, lastActivity: 50_000, lastPing: 0, idleMs })).toBe(
      'none',
    )
  })
})
