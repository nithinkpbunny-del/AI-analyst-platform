import { ApiError, authedRequest, request } from './api'
import type { Role } from './role'

// ------------------------------------------------------------------------------------------------ API types

export type TwoFactorMethod = 'totp' | 'email'

export interface TwoFactorInfo {
  enabled: boolean
  method: TwoFactorMethod | null
  /** Owners must have it on; the app sends them to set-up until it is. */
  required: boolean
  recovery_codes_remaining: number
}

export interface SessionInfo {
  user: {
    id: string
    email: string
    name: string
    preferred_language: string
    has_password: boolean
    google_linked: boolean
  }
  organization: { id: string; name: string; timezone: string; currency: string; plan: string }
  role: Role
  memberships: { organization_id: string; organization_name: string; role: Role }[]
  session_expires_at: string
  idle_timeout_minutes: number
  two_factor: TwoFactorInfo
}

export interface SessionResponse extends SessionInfo {
  status: 'signed_in'
  access_token: string
  token_type: 'bearer'
  expires_in: number
  csrf_token: string
}

/** The first step (password or Google) worked; a code is needed, or an Owner must set up 2FA first. */
export interface ChallengeResponse {
  status: 'two_factor_required' | 'two_factor_setup_required'
  challenge_token: string
  method: TwoFactorMethod | null
  email_hint: string | null
  code_sent: boolean
  expires_in: number
}

export type LoginResponse = SessionResponse | ChallengeResponse

export interface SetupInfo {
  method: TwoFactorMethod
  secret: string | null
  otpauth_uri: string | null
  /** QR code modules: one string of "0"/"1" per row. */
  qr: string[] | null
  email_hint: string | null
}

export interface SetupDoneResponse extends SessionResponse {
  recovery_codes: string[]
}

export interface SessionItem {
  id: string
  current: boolean
  browser: string
  ip: string | null
  created_at: string
  last_seen_at: string
}

export interface Providers {
  google: boolean
  google_stand_in: boolean
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
    request<LoginResponse>('/auth/login', { method: 'POST', body: { email, password } }),
  loginVerify: (challenge_token: string, code: string) =>
    request<SessionResponse>('/auth/login/verify', {
      method: 'POST',
      body: { challenge_token, code },
    }),
  loginResendCode: (challenge_token: string) =>
    request<ChallengeResponse>('/auth/login/resend-code', {
      method: 'POST',
      body: { challenge_token },
    }),
  loginSetup: (challenge_token: string, method: TwoFactorMethod) =>
    request<SetupInfo>('/auth/login/setup', { method: 'POST', body: { challenge_token, method } }),
  loginSetupResend: (challenge_token: string) =>
    request<Accepted>('/auth/login/setup/resend-code', {
      method: 'POST',
      body: { challenge_token },
    }),
  loginSetupConfirm: (challenge_token: string, code: string) =>
    request<SetupDoneResponse>('/auth/login/setup/confirm', {
      method: 'POST',
      body: { challenge_token, code },
    }),
  providers: () => request<Providers>('/auth/providers'),
  googleFinish: (token: string) =>
    request<LoginResponse>('/auth/google/finish', { method: 'POST', body: { token } }),
  googleSignupInfo: (token: string) =>
    request<{ email: string; name: string }>('/auth/google/signup-info', {
      method: 'POST',
      body: { token },
    }),
  googleSignup: (body: {
    token: string
    name: string
    organization_name: string
    accept_terms: boolean
  }) => request<LoginResponse>('/auth/google/signup', { method: 'POST', body }),
  secureAccount: (token: string) =>
    request<Done>('/auth/secure-account', { method: 'POST', body: { token } }),
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
  // Signed-in security settings.
  setup: (method: TwoFactorMethod, code?: string) =>
    authedRequest<SetupInfo>('/auth/2fa/setup', { method: 'POST', body: { method, code } }),
  setupResend: () => authedRequest<Accepted>('/auth/2fa/setup/resend-code', { method: 'POST' }),
  setupConfirm: (code: string) =>
    authedRequest<{ recovery_codes: string[] }>('/auth/2fa/setup/confirm', {
      method: 'POST',
      body: { code },
    }),
  sendCode: () => authedRequest<Accepted>('/auth/2fa/send-code', { method: 'POST' }),
  newRecoveryCodes: (code: string) =>
    authedRequest<{ recovery_codes: string[] }>('/auth/2fa/recovery-codes', {
      method: 'POST',
      body: { code },
    }),
  disableTwoFactor: (code: string) =>
    authedRequest<Done>('/auth/2fa/disable', { method: 'POST', body: { code } }),
  sessions: () => authedRequest<{ sessions: SessionItem[] }>('/auth/sessions'),
  revokeSession: (id: string) =>
    authedRequest<void>(`/auth/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  revokeOtherSessions: () =>
    authedRequest<{ sessions_revoked: number }>('/auth/sessions/revoke-others', {
      method: 'POST',
    }),
}

export function isChallenge(response: LoginResponse): response is ChallengeResponse {
  return response.status !== 'signed_in'
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
