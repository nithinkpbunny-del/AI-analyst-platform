import type { SessionInfo } from './authApi'
import { ROLE_PERMISSIONS } from './permissions'

/** A signed-in Owner of the UrbanCart demo shop, for Storybook, component tests and screenshots. */
export const demoSession: SessionInfo = {
  user: {
    id: '0196f000-0000-7000-8000-000000000001',
    email: 'asha@urbancart.in',
    name: 'Asha Rao',
    preferred_language: 'en',
    has_password: true,
    google_linked: false,
  },
  organization: {
    id: '0196f000-0000-7000-8000-0000000000a1',
    name: 'UrbanCart',
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    plan: 'managed',
  },
  role: 'owner',
  memberships: [
    {
      organization_id: '0196f000-0000-7000-8000-0000000000a1',
      organization_name: 'UrbanCart',
      role: 'owner',
    },
  ],
  session_expires_at: '2026-11-09T18:30:00+00:00',
  idle_timeout_minutes: 30,
  two_factor: { enabled: true, method: 'totp', required: true, recovery_codes_remaining: 10 },
  permissions: ROLE_PERMISSIONS.owner,
}
