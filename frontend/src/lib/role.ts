export type Role = 'owner' | 'analyst' | 'viewer'

/** The signed-in user's role. Authentication arrives in M2; until then the shell shows the Owner view. */
export function useRole(): Role {
  return 'owner'
}
