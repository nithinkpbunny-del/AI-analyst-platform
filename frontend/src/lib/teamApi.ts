import {
  API_BASE_URL,
  ApiError,
  authedDownload,
  authedRequest,
  getAccessToken,
  request,
} from './api'
import type { LoginResponse, SwitchedResponse } from './authApi'
import type { Role } from './role'

// ------------------------------------------------------------------------------------------------ team
export type InviteRole = 'analyst' | 'viewer'

export interface Member {
  /** The membership id (used in /team/members/{id}). */
  id: string
  user_id: string
  name: string
  email: string
  role: Role
  two_factor_enabled: boolean
  joined_at: string
  is_you: boolean
}

export interface Invitation {
  id: string
  email: string
  role: InviteRole
  invited_by_name: string | null
  created_at: string
  expires_at: string
  expired: boolean
}

export interface InvitationPreview {
  organization_name: string
  invited_by_name: string | null
  email: string
  role: InviteRole
  expires_at: string
  account_exists: boolean
  has_password: boolean
}

export type Industry =
  'retail' | 'd2c' | 'restaurant' | 'fuel_station' | 'services' | 'manufacturing' | 'other'
export const INDUSTRIES: Industry[] = [
  'retail',
  'd2c',
  'restaurant',
  'fuel_station',
  'services',
  'manufacturing',
  'other',
]

export interface OrganizationSettings {
  id: string
  name: string
  timezone: string
  currency: string
  plan: string
  industry: Industry | null
  /** A requested deletion (BUILD_SPEC 6.6): when it was asked and when it happens. */
  deletion_requested_at: string | null
  deletion_due_at: string | null
}

// -------------------------------------------------------------------------------------------- activity
export interface AuditEntry {
  id: string
  at: string
  action: string
  user_id: string | null
  user_name: string | null
  user_email: string | null
  target_type: string | null
  target_id: string | null
  ip: string | null
  browser: string | null
  details: Record<string, unknown>
}

export interface AuditPage {
  entries: AuditEntry[]
  next_cursor: string | null
  scope: 'workspace' | 'own'
}

export interface AuditFilters {
  user_id?: string
  action?: string
  from?: string
  to?: string
}

export function auditQuery(filters: AuditFilters, cursor?: string | null): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
  if (cursor) params.set('cursor', cursor)
  const query = params.toString()
  return query ? `?${query}` : ''
}

const id = (value: string) => encodeURIComponent(value)

export const teamApi = {
  members: () => authedRequest<{ members: Member[] }>('/team/members'),
  changeRole: (membershipId: string, role: Role) =>
    authedRequest<Member>(`/team/members/${id(membershipId)}`, {
      method: 'PATCH',
      body: { role },
    }),
  removeMember: (membershipId: string) =>
    authedRequest<void>(`/team/members/${id(membershipId)}`, { method: 'DELETE' }),
  invitations: () => authedRequest<{ invitations: Invitation[] }>('/team/invitations'),
  invite: (email: string, role: InviteRole) =>
    authedRequest<Invitation>('/team/invitations', { method: 'POST', body: { email, role } }),
  resendInvitation: (invitationId: string) =>
    authedRequest<Invitation>(`/team/invitations/${id(invitationId)}/resend`, { method: 'POST' }),
  revokeInvitation: (invitationId: string) =>
    authedRequest<void>(`/team/invitations/${id(invitationId)}`, { method: 'DELETE' }),

  organization: () => authedRequest<OrganizationSettings>('/org'),
  updateOrganization: (changes: { name?: string; timezone?: string; industry?: Industry }) =>
    authedRequest<OrganizationSettings>('/org', { method: 'PATCH', body: changes }),
  requestDeletion: (confirmName: string) =>
    authedRequest<OrganizationSettings>('/org/deletion', {
      method: 'POST',
      body: { confirm_name: confirmName },
    }),
  cancelDeletion: () => authedRequest<OrganizationSettings>('/org/deletion', { method: 'DELETE' }),
  exportData: () => authedDownload('/org/export.zip'),

  audit: (filters: AuditFilters, cursor?: string | null) =>
    authedRequest<AuditPage>(`/audit${auditQuery(filters, cursor)}`),
}

/** Accepting an invitation (the token comes from the email link's #fragment). */
export const invitationApi = {
  preview: (token: string) =>
    request<InvitationPreview>('/invitations/preview', { method: 'POST', body: { token } }),
  acceptNew: (body: { token: string; name: string; password: string; accept_terms: boolean }) =>
    request<LoginResponse>('/invitations/accept', { method: 'POST', body }),
  acceptWithPassword: (token: string, password: string) =>
    request<LoginResponse>('/invitations/accept-sign-in', {
      method: 'POST',
      body: { token, password },
    }),
  acceptSignedIn: (token: string) =>
    authedRequest<SwitchedResponse>('/invitations/accept-signed-in', {
      method: 'POST',
      body: { token },
    }),
}

/**
 * Download the filtered activity log as CSV. The file needs the access token, so it is fetched here and handed to
 * the browser as a blob rather than opened as a link.
 */
export async function downloadAuditCsv(filters: AuditFilters): Promise<void> {
  const fetchOnce = () =>
    fetch(`${API_BASE_URL}/audit/export.csv${auditQuery(filters)}`, {
      headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
      credentials: 'include',
    })
  let response = await fetchOnce()
  if (response.status === 401) {
    // Expired access token: any authed call refreshes it, then try once more.
    await authedRequest('/auth/me').catch(() => undefined)
    response = await fetchOnce()
  }
  if (!response.ok) {
    throw new ApiError(response.status, 'export_failed', 'The export did not work. Try again.')
  }
  const blob = await response.blob()
  const disposition = response.headers.get('content-disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'activity.csv'
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
