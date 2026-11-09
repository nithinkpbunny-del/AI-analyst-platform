import { useContext } from 'react'
import { AuthContext } from './authContext'

export type Role = 'owner' | 'analyst' | 'viewer'

/** The signed-in user's role in the current organisation. Outside a signed-in session: the least access. */
export function useRole(): Role {
  const auth = useContext(AuthContext)
  return auth?.state.status === 'signedIn' ? auth.state.session.role : 'viewer'
}
