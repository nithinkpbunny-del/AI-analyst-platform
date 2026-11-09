import { useContext } from 'react'
import { AuthContext } from './authContext'
import type { Role } from './role'

/** BUILD_SPEC 6.2 permissions, as `/auth/me` sends them (backend `app/auth/permissions.py`). */
export type Permission =
  | 'org.manage'
  | 'members.manage'
  | 'connections.manage'
  | 'dictionary.edit'
  | 'dictionary.suggest'
  | 'ask'
  | 'saved_questions'
  | 'share'
  | 'export'
  | 'goals.edit'
  | 'goals.view'
  | 'dashboard.edit'
  | 'dashboard.view'
  | 'shared.view'
  | 'audit.view_all'
  | 'audit.view_own'

/** The list the API sends for each role (Owners with two-step verification on). For demos and tests only. */
export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  viewer: ['dashboard.view', 'export', 'goals.view', 'shared.view'],
  analyst: [
    'ask',
    'audit.view_own',
    'dashboard.edit',
    'dashboard.view',
    'dictionary.suggest',
    'export',
    'goals.edit',
    'goals.view',
    'saved_questions',
    'share',
    'shared.view',
  ],
  owner: [
    'ask',
    'audit.view_all',
    'audit.view_own',
    'connections.manage',
    'dashboard.edit',
    'dashboard.view',
    'dictionary.edit',
    'dictionary.suggest',
    'export',
    'goals.edit',
    'goals.view',
    'members.manage',
    'org.manage',
    'saved_questions',
    'share',
    'shared.view',
  ],
}

/** May the signed-in person do this? Only shows or hides UI; the API checks every request itself. */
export function useCan(permission: Permission): boolean {
  const auth = useContext(AuthContext)
  return auth?.state.status === 'signedIn' && auth.state.session.permissions.includes(permission)
}
