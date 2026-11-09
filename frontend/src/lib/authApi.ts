import { ApiError, authedRequest, request } from './api'
import type { Role } from './role'

// ------------------------------------------------------------------------------------------------ API types

export interface SessionInfo {
  user: { id: string; email: string; name: string; preferred_language: string }
  organization: { id: string; name: string; timezone: string; currency: string; plan: string }
  role: Role
  memberships: { organization_id: string; organization_name: string; role: Role }[]
  session_expires_at: string
  idle_timeout_minutes: number
}

export interface SessionResponse extends SessionInfo {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  csrf_token: string
}

export interface Accepted {
  status: 'accepted'
  message: string
}

export interface Done {
  status: 'ok'
  message: string
}

export interface SignupInput {
  name: string
  email: string
  password: string
  organization_name: string
  accept_terms: boolean
}

export const authApi = {
  csrf: () => request<{ csrf_token: string }>('/auth/csrf'),
  signup: (body: SignupInput) => request<Accepted>('/auth/signup', { method: 'POST', body }),
  verifyEmail: (token: string) =>
    request<Done>('/auth/verify-email', { method: 'POST', body: { token } }),
  resendVerification: (email: string) =>
    request<Accepted>('/auth/resend-verification', { method: 'POST', body: { email } }),
  login: (email: string, password: string) =>
    request<SessionResponse>('/auth/login', { method: 'POST', body: { email, password } }),
  refresh: (csrf: string) =>
    request<SessionResponse>('/auth/refresh', {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrf },
    }),
  logout: (csrf: string) =>
    request<void>('/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf } }),
  forgotPassword: (email: string) =>
    request<Accepted>('/auth/forgot-password', { method: 'POST', body: { email } }),
  resetPassword: (token: string, password: string) =>
    request<Done>('/auth/reset-password', { method: 'POST', body: { token, password } }),
  me: () => authedRequest<SessionInfo>('/auth/me'),
}

// ------------------------------------------------------------------------------------------------- refresh

/** Codes that mean "your session ended" and deserve an explanation on the sign-in screen. */
export const ENDED_CODES = new Set(['session_idle', 'session_expired', 'session_revoked'])

let inFlight: Promise<SessionResponse> | null = null

async function refreshOnce(): Promise<SessionResponse> {
  // The CSRF cookie may have changed in another tab (sign-out, sign-in), so read it fresh each time.
  const { csrf_token } = await authApi.csrf()
  return authApi.refresh(csrf_token)
}

/**
 * One refresh at a time. Refresh tokens rotate and a reused one ends the session, so two tabs (or React's
 * double effects in development) must never refresh in parallel: share the in-flight call within a tab and
 * use the Web Locks API across tabs, so the second caller sends the already-rotated cookie.
 */
export function refreshSession(): Promise<SessionResponse> {
  if (inFlight) return inFlight
  const run = () => refreshOnce()
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
  inFlight = (locks ? locks.request('analyst-auth-refresh', run) : run()).finally(() => {
    inFlight = null
  })
  return inFlight
}

// ---------------------------------------------------------------------------------------- activity tracking

const PING_EVERY_MS = 4 * 60_000

/**
 * The server ends a session after `idle_timeout_minutes` without API calls. Screens that make no calls would
 * let an active person time out, and an open but unused tab should still sign out. So: sign out after the idle
 * time without any interaction, and while the person is active, tell the server every few minutes.
 */
export function activityDecision(args: {
  now: number
  lastActivity: number
  lastPing: number
  idleMs: number
}): 'sign-out' | 'ping' | 'none' {
  const { now, lastActivity, lastPing, idleMs } = args
  if (now - lastActivity >= idleMs) return 'sign-out'
  if (lastActivity > lastPing && now - lastPing >= PING_EVERY_MS) return 'ping'
  return 'none'
}

/** The message to show on the sign-in screen after a failed refresh, if any. */
export function endedReason(error: unknown): string | null {
  return error instanceof ApiError && ENDED_CODES.has(error.code) ? error.message : null
}
