import { createContext, useContext } from 'react'
import type { ChallengeResponse, LoginResponse, SessionInfo } from './authApi'

export type AuthState =
  | { status: 'loading' }
  | { status: 'signedIn'; session: SessionInfo }
  | { status: 'signedOut'; reason: string | null }

/** A sign-in waiting for its second step, kept in memory only (never in storage). */
export interface PendingChallenge extends ChallengeResponse {
  /** Where to go once signed in. */
  next: string
}

export interface AuthContextValue {
  state: AuthState
  /** The second step that is waiting, if any. */
  challenge: PendingChallenge | null
  /** Handle any sign-in answer (password, Google, 2FA): sign in, or keep the challenge. */
  acceptLogin: (response: LoginResponse, next?: string) => void
  signOut: () => Promise<void>
  /** Re-read the session (e.g. after turning on 2FA in Settings). */
  reload: () => Promise<void>
  /** Clear the "you were signed out because…" note once it has been shown. */
  clearReason: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}

/** The signed-in session; only call inside routes guarded by <RequireAuth>. */
export function useSession(): SessionInfo {
  const { state } = useAuth()
  if (state.status !== 'signedIn') throw new Error('useSession needs a signed-in user')
  return state.session
}

/** A do-nothing auth context value for Storybook and component tests. */
export function staticAuthValue(
  state: AuthState,
  challenge: PendingChallenge | null = null,
): AuthContextValue {
  return {
    state,
    challenge,
    acceptLogin: () => {},
    signOut: async () => {},
    reload: async () => {},
    clearReason: () => {},
  }
}
