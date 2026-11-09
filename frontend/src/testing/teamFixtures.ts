import type { AuditEntry, Invitation, InvitationPreview, Member } from '../lib/teamApi'

// Fixed demo data for the Team and Activity screens (component tests, Storybook and screenshots).

export const demoMembers: Member[] = [
  {
    id: 'm1',
    user_id: 'u1',
    name: 'Asha Rao',
    email: 'asha@urbancart.in',
    role: 'owner',
    two_factor_enabled: true,
    joined_at: '2026-06-02T05:30:00+00:00',
    is_you: true,
  },
  {
    id: 'm2',
    user_id: 'u2',
    name: 'Ravi Kumar',
    email: 'ravi@urbancart.in',
    role: 'analyst',
    two_factor_enabled: false,
    joined_at: '2026-08-14T06:00:00+00:00',
    is_you: false,
  },
  {
    id: 'm3',
    user_id: 'u3',
    name: 'Lata Menon',
    email: 'lata@urbancart.in',
    role: 'viewer',
    two_factor_enabled: true,
    joined_at: '2026-10-21T09:15:00+00:00',
    is_you: false,
  },
]

export const demoInvitations: Invitation[] = [
  {
    id: 'i1',
    email: 'dev@urbancart.in',
    role: 'analyst',
    invited_by_name: 'Asha Rao',
    created_at: '2026-10-28T04:30:00+00:00',
    expires_at: '2026-11-04T04:30:00+00:00',
    expired: true,
  },
  {
    id: 'i2',
    email: 'meera@urbancart.in',
    role: 'viewer',
    invited_by_name: 'Asha Rao',
    created_at: '2026-10-30T04:30:00+00:00',
    expires_at: '2026-11-06T04:30:00+00:00',
    expired: false,
  },
]

const ua = { ip: '203.0.113.24', browser: 'Chrome on macOS' }

export const demoAudit: AuditEntry[] = [
  {
    id: 'a1',
    at: '2026-10-30T05:12:00+00:00',
    action: 'team.role_changed',
    user_id: 'u1',
    user_name: 'Asha Rao',
    user_email: 'asha@urbancart.in',
    target_type: 'user',
    target_id: 'u2',
    details: { from: 'viewer', to: 'analyst', email: 'ravi@urbancart.in' },
    ...ua,
  },
  {
    id: 'a2',
    at: '2026-10-30T04:58:00+00:00',
    action: 'team.invite_sent',
    user_id: 'u1',
    user_name: 'Asha Rao',
    user_email: 'asha@urbancart.in',
    target_type: 'invitation',
    target_id: 'i2',
    details: { email: 'meera@urbancart.in', role: 'viewer' },
    ...ua,
  },
  {
    id: 'a3',
    at: '2026-10-30T04:31:00+00:00',
    action: 'auth.login.success',
    user_id: 'u1',
    user_name: 'Asha Rao',
    user_email: 'asha@urbancart.in',
    target_type: 'session',
    target_id: 's1',
    details: { method: 'password', two_factor: 'totp', new_device: false },
    ...ua,
  },
  {
    id: 'a4',
    at: '2026-10-29T13:05:00+00:00',
    action: 'auth.login.failure',
    user_id: 'u2',
    user_name: 'Ravi Kumar',
    user_email: 'ravi@urbancart.in',
    target_type: 'user',
    target_id: 'u2',
    details: { reason: 'bad_password' },
    ip: '198.51.100.7',
    browser: 'Safari on iPhone',
  },
  {
    id: 'a5',
    at: '2026-10-29T12:40:00+00:00',
    action: 'auth.2fa.enabled',
    user_id: 'u3',
    user_name: 'Lata Menon',
    user_email: 'lata@urbancart.in',
    target_type: null,
    target_id: null,
    details: { method: 'email' },
    ip: '198.51.100.22',
    browser: 'Firefox on Windows',
  },
]

export const demoPreview: InvitationPreview = {
  organization_name: 'UrbanCart',
  invited_by_name: 'Asha Rao',
  email: 'dev@urbancart.in',
  role: 'analyst',
  expires_at: '2026-11-16T04:30:00+00:00',
  account_exists: false,
  has_password: false,
}
