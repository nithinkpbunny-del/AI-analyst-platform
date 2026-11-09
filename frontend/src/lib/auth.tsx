import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError, setAccessToken, setRefreshHandler } from './api'
import {
  activityDecision,
  authApi,
  ENDED_CODES,
  endedReason,
  refreshSession,
  type SessionInfo,
  type SessionResponse,
} from './authApi'

// The API helpers live in ./authApi; this file holds React state only (keeps fast refresh working).
import { AuthContext, type AuthContextValue, type AuthState } from './authContext'

function toSessionInfo(response: SessionResponse): SessionInfo {
  return {
    user: response.user,
    organization: response.organization,
    role: response.role,
    memberships: response.memberships,
    session_expires_at: response.session_expires_at,
    idle_timeout_minutes: response.idle_timeout_minutes,
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })

  const accept = useCallback((response: SessionResponse) => {
    setAccessToken(response.access_token)
    setState({ status: 'signedIn', session: toSessionInfo(response) })
  }, [])

  const end = useCallback((reason: string | null) => {
    setAccessToken(null)
    setState({ status: 'signedOut', reason })
  }, [])

  const refresh = useCallback(
    (): Promise<string | null> =>
      refreshSession().then(
        (response) => {
          accept(response)
          return response.access_token
        },
        (error: unknown) => {
          end(endedReason(error))
          return null
        },
      ),
    [accept, end],
  )

  // On load, the HttpOnly refresh cookie (if any) is exchanged for an access token.
  useEffect(() => {
    setRefreshHandler(refresh)
    refreshSession().then(accept, (error: unknown) => end(endedReason(error)))
    return () => setRefreshHandler(null)
  }, [refresh, accept, end])

  const signIn = useCallback(
    async (email: string, password: string) => {
      accept(await authApi.login(email, password))
    },
    [accept],
  )

  const signOut = useCallback(async () => {
    try {
      const { csrf_token } = await authApi.csrf()
      await authApi.logout(csrf_token)
    } catch {
      /* signed out locally either way */
    }
    end(null)
  }, [end])

  const clearReason = useCallback(() => {
    setState((current) =>
      current.status === 'signedOut' && current.reason
        ? { status: 'signedOut', reason: null }
        : current,
    )
  }, [])

  // Idle sign-out and activity pings, only while signed in.
  const idleMinutes = state.status === 'signedIn' ? state.session.idle_timeout_minutes : null
  const lastActivity = useRef(0)
  const lastPing = useRef(0)
  useEffect(() => {
    if (idleMinutes === null) return
    lastActivity.current = Date.now()
    lastPing.current = Date.now()
    const mark = () => {
      lastActivity.current = Date.now()
    }
    const events = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const
    events.forEach((name) => window.addEventListener(name, mark, { passive: true }))
    const timer = window.setInterval(() => {
      const decision = activityDecision({
        now: Date.now(),
        lastActivity: lastActivity.current,
        lastPing: lastPing.current,
        idleMs: idleMinutes * 60_000,
      })
      if (decision === 'sign-out') {
        void signOut().then(() =>
          end(`You were signed out after ${idleMinutes} minutes without activity. Sign in again.`),
        )
      } else if (decision === 'ping') {
        lastPing.current = Date.now()
        authApi.me().catch((error: unknown) => {
          if (error instanceof ApiError && error.status === 401) {
            end(ENDED_CODES.has(error.code) ? error.message : null)
          }
        })
      }
    }, 30_000)
    return () => {
      window.clearInterval(timer)
      events.forEach((name) => window.removeEventListener(name, mark))
    }
  }, [idleMinutes, signOut, end])

  const value = useMemo(
    () => ({ state, signIn, signOut, clearReason }),
    [state, signIn, signOut, clearReason],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** A fixed signed-in session, for Storybook and component tests that render the shell without an API. */
export function StaticAuthProvider({
  session,
  children,
}: {
  session: SessionInfo
  children: ReactNode
}) {
  const value = useMemo<AuthContextValue>(
    () => ({
      state: { status: 'signedIn', session },
      signIn: async () => {},
      signOut: async () => {},
      clearReason: () => {},
    }),
    [session],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
