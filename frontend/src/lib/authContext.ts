import { createContext, useContext } from 'react'
import type { SessionInfo } from './authApi'

export type AuthState =
  | { status: 'loading' }
  | { status: 'signedIn'; session: SessionInfo }
  | { status: 'signedOut'; reason: string | null }

export interface AuthContextValue {
  state: AuthState
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
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
