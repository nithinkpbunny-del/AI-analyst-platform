import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { isChallenge, type LoginResponse } from '../../lib/authApi'
import { useAuth } from '../../lib/authContext'

/** Where to go after signing in: the page the person was sent away from, if it is one of ours. */
export function safeNext(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')
    ? value
    : '/today'
}

/** The page someone was on before Google sign-in (kept in sessionStorage across the redirect). */
const NEXT_KEY = 'analyst-next-after-google'

export function rememberNext(next: string) {
  try {
    window.sessionStorage.setItem(NEXT_KEY, next)
  } catch {
    /* storage blocked: we land on Today instead */
  }
}

export function takeRememberedNext(): string {
  try {
    const value = window.sessionStorage.getItem(NEXT_KEY)
    window.sessionStorage.removeItem(NEXT_KEY)
    return safeNext(value)
  } catch {
    return '/today'
  }
}

/** Handle any sign-in answer: go to the app, to the code step, or to 2FA set-up. */
export function useFinishLogin() {
  const { acceptLogin } = useAuth()
  const navigate = useNavigate()
  return useCallback(
    (response: LoginResponse, next: string) => {
      acceptLogin(response, next)
      if (!isChallenge(response)) void navigate(next, { replace: true })
      else if (response.status === 'two_factor_required') void navigate('/signin/verify')
      else void navigate('/signin/setup')
    },
    [acceptLogin, navigate],
  )
}

/** "123 456" or "123-456" → "123456". */
export function cleanCode(value: string): string {
  return value.replace(/[\s-]/g, '')
}

const ERROR_CODES = [
  'google_cancelled',
  'google_failed',
  'google_expired',
  'google_unavailable',
  'google_email_unverified',
  'google_other_account',
  'no_organization',
] as const

/** The message for `/signin?google=<code>` after a failed Google sign-in. */
export function useGoogleError(search: string): string | null {
  const { t } = useTranslation()
  const code = new URLSearchParams(search).get('google')
  if (!code) return null
  const known = (ERROR_CODES as readonly string[]).includes(code) ? code : 'google_failed'
  return t(`auth.google.errors.${known}`)
}
