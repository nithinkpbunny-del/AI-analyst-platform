import type { Page } from '@playwright/test'
import { ROLE_PERMISSIONS } from '../src/lib/permissions'
import { demoAiSettings, demoUsage } from '../src/testing/aiFixtures'
import { demoBilling, demoSummary } from '../src/testing/billingFixtures'
import { demoTemplates } from '../src/testing/templatesFixtures'
import {
  demoAccess,
  demoConnectionList,
  demoOrdersDetail,
  demoDatasetPreview,
  demoZohoDetail,
  demoDatabaseDetail,
  demoDatabaseTables,
} from '../src/testing/connectionFixtures'
import { demoConversation, demoConversations, demoSaved } from '../src/testing/askFixtures'
import { firstDraftState } from '../src/testing/dictionaryFixtures'
import { demoAudit, demoInvitations, demoMembers, demoPreview } from '../src/testing/teamFixtures'
import { demoOptions, demoResult } from '../src/testing/whatIfFixtures'
import { demoDashboard, demoGoalList, demoGoalOptions } from '../src/testing/goalsFixtures'
import {
  demoBriefing,
  demoBriefingSettings,
  demoOrg,
  demoRuns,
  demoShares,
  demoTaskReport,
  demoTasks,
  demoToday,
} from '../src/testing/reportsFixtures'

export const screens = [
  'today',
  'ask',
  'dashboard',
  'what-if',
  'goals',
  'briefings',
  'tasks',
  'connections',
  'dictionary',
  'team',
  'activity',
  'settings',
] as const

/** Signed-out screens (BUILD_SPEC 11: sign up, login, forgot password; the email-link screens). */
export const authScreens = [
  'signin',
  'signup',
  'check-email',
  'forgot-password',
  'reset-password',
  'verify-email',
  'secure-account',
] as const

const API = 'http://localhost:8000'

/** The UrbanCart demo Owner (same as src/lib/demoSession.ts) as /auth/refresh returns it. */
const session = {
  status: 'signed_in',
  user: {
    id: 'u1',
    email: 'asha@urbancart.in',
    name: 'Asha Rao',
    preferred_language: 'en',
    has_password: true,
    google_linked: false,
  },
  organization: {
    id: 'o1',
    name: 'UrbanCart',
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    plan: 'managed',
  },
  role: 'owner',
  memberships: [{ organization_id: 'o1', organization_name: 'UrbanCart', role: 'owner' }],
  session_expires_at: '2026-11-09T18:30:00+00:00',
  idle_timeout_minutes: 30,
  two_factor: { enabled: true, method: 'totp', required: true, recovery_codes_remaining: 10 },
  permissions: ROLE_PERMISSIONS.owner,
  access_token: 'e2e-access-token',
  token_type: 'bearer',
  expires_in: 600,
  csrf_token: 'e2e-csrf',
}

/** An Owner who has not turned on two-step verification yet (sent to set-up). */
export const sessionWithout2fa = {
  ...session,
  two_factor: { enabled: false, method: null, required: true, recovery_codes_remaining: 0 },
  permissions: [],
}

/** An Analyst of UrbanCart who is also a Viewer at a second shop (workspace switcher, own activity). */
export const analystSession = {
  ...session,
  user: { ...session.user, id: 'u2', email: 'ravi@urbancart.in', name: 'Ravi Kumar' },
  role: 'analyst',
  permissions: ROLE_PERMISSIONS.analyst,
  memberships: [
    { organization_id: 'o1', organization_name: 'UrbanCart', role: 'analyst' },
    { organization_id: 'o2', organization_name: 'Chaiwala Foods', role: 'viewer' },
  ],
  two_factor: { enabled: false, method: null, required: false, recovery_codes_remaining: 0 },
}

export const invitationToken = 'e2einvitationtoken12345'
export const invitationPreview = demoPreview

export const codeChallenge = {
  status: 'two_factor_required',
  challenge_token: 'e2e-challenge',
  method: 'totp',
  email_hint: 'as***@urbancart.in',
  code_sent: false,
  expires_in: 900,
}

// Dates older than a week render as fixed dates ("30 Oct 2026"), so screenshots stay stable.
const devices = {
  sessions: [
    {
      id: 's1',
      current: true,
      browser: 'Chrome on macOS',
      ip: '203.0.113.24',
      created_at: '2026-10-30T04:30:00+00:00',
      last_seen_at: '2026-10-30T04:30:00+00:00',
    },
    {
      id: 's2',
      current: false,
      browser: 'Safari on iPhone',
      ip: '198.51.100.7',
      created_at: '2026-10-28T13:10:00+00:00',
      last_seen_at: '2026-10-29T08:45:00+00:00',
    },
  ],
}

/** Answer API calls with fixed data so screenshots never depend on a running backend. */
interface MockOptions {
  signedIn?: boolean
  refresh?: object
  /** The /audit answer (default: the whole workspace's demo entries). */
  audit?: object
}

export async function mockApi(
  page: Page,
  { signedIn = true, refresh = session, audit }: MockOptions = {},
) {
  const auditReply = audit ?? { entries: demoAudit, next_cursor: null, scope: 'workspace' }
  await page.route(`${API}/auth/csrf`, (route) =>
    route.fulfill({ json: { csrf_token: 'e2e-csrf' } }),
  )
  await page.route(`${API}/auth/refresh`, (route) =>
    signedIn
      ? route.fulfill({ json: refresh })
      : route.fulfill({
          status: 401,
          json: { error: { code: 'not_signed_in', message: 'Sign in to continue.' } },
        }),
  )
  await page.route(`${API}/auth/verify-email`, (route) =>
    route.fulfill({
      status: 400,
      json: { error: { code: 'invalid_link', message: 'Expired.' } },
    }),
  )
  await page.route(`${API}/auth/providers`, (route) =>
    route.fulfill({ json: { google: true, google_stand_in: false } }),
  )
  await page.route(`${API}/auth/sessions`, (route) => route.fulfill({ json: devices }))
  await page.route(`${API}/team/members`, (route) =>
    route.fulfill({ json: { members: demoMembers } }),
  )
  await page.route(`${API}/team/invitations`, (route) =>
    route.fulfill({ json: { invitations: demoInvitations } }),
  )
  await page.route(`${API}/audit?*`, (route) => route.fulfill({ json: auditReply }))
  await page.route(`${API}/audit`, (route) => route.fulfill({ json: auditReply }))
  await page.route(`${API}/invitations/preview`, (route) => route.fulfill({ json: demoPreview }))
  await page.route(`${API}/org/ai`, (route) => route.fulfill({ json: demoAiSettings }))
  await page.route(`${API}/org/usage*`, (route) => route.fulfill({ json: demoUsage }))
  // M17/M18: industry templates, plan and billing (hooks only; nothing is charged).
  await page.route(`${API}/org/templates`, (route) => route.fulfill({ json: demoTemplates }))
  await page.route(`${API}/org/billing`, (route) => route.fulfill({ json: demoBilling }))
  await page.route(`${API}/org/billing/summary*`, (route) => route.fulfill({ json: demoSummary }))
  await page.route(`${API}/connections`, (route) => route.fulfill({ json: demoConnectionList }))
  await page.route(`${API}/connections/access`, (route) =>
    route.fulfill({ json: { people: demoAccess } }),
  )
  await page.route(`${API}/connections/c1`, (route) => route.fulfill({ json: demoOrdersDetail }))
  await page.route(`${API}/connections/c6`, (route) => route.fulfill({ json: demoZohoDetail }))
  await page.route(`${API}/connections/c7`, (route) => route.fulfill({ json: demoDatabaseDetail }))
  await page.route(`${API}/connections/c7/database/tables`, (route) =>
    route.fulfill({ json: demoDatabaseTables }),
  )
  await page.route(`${API}/connections/c1/datasets/d1/preview`, (route) =>
    route.fulfill({ json: demoDatasetPreview }),
  )
  await page.route(`${API}/dictionary`, (route) => route.fulfill({ json: firstDraftState }))
  await page.route(`${API}/conversations`, (route) =>
    route.fulfill({ json: { conversations: demoConversations } }),
  )
  await page.route(`${API}/conversations/conv1`, (route) =>
    route.fulfill({ json: demoConversation }),
  )
  // M9: a fixed what-if (dates older than a week render as fixed dates, so screenshots stay stable).
  const whatIf = demoResult()
  whatIf.tables = whatIf.tables.map((table) => ({
    ...table,
    fetched_at: '2026-10-30T04:30:00+00:00',
  }))
  await page.route(`${API}/whatif/options`, (route) => route.fulfill({ json: demoOptions }))
  await page.route(`${API}/whatif/values?*`, (route) =>
    route.fulfill({ json: { values: ['Electronics', 'Fashion', 'Home & Kitchen'] } }),
  )
  await page.route(`${API}/whatif/run`, (route) => route.fulfill({ json: whatIf }))
  // M10: Today, briefings, long tasks, reports and sharing.
  await page.route(`${API}/today`, (route) => route.fulfill({ json: demoToday }))
  await page.route(`${API}/org`, (route) => route.fulfill({ json: demoOrg() }))
  await page.route(`${API}/tasks`, (route) => route.fulfill({ json: demoTasks }))
  await page.route(`${API}/briefings/settings`, (route) =>
    route.fulfill({ json: demoBriefingSettings }),
  )
  await page.route(`${API}/briefings`, (route) => route.fulfill({ json: { runs: demoRuns } }))
  await page.route(`${API}/reports/rep-briefing`, (route) => route.fulfill({ json: demoBriefing }))
  await page.route(`${API}/reports/rep-task`, (route) => route.fulfill({ json: demoTaskReport }))
  await page.route(`${API}/shares?*`, (route) => route.fulfill({ json: demoShares }))
  // M15: goals and the dashboard ("Checked"/"Updated" times older than a week render as fixed dates).
  await page.route(`${API}/goals`, (route) => route.fulfill({ json: demoGoalList }))
  await page.route(`${API}/goals/refresh`, (route) => route.fulfill({ json: demoGoalList }))
  await page.route(`${API}/goals/options`, (route) => route.fulfill({ json: demoGoalOptions }))
  await page.route(`${API}/dashboard`, (route) => route.fulfill({ json: demoDashboard }))
  await page.route(`${API}/dashboard/refresh`, (route) => route.fulfill({ json: demoDashboard }))
  await page.route(`${API}/saved-questions`, (route) =>
    route.fulfill({ json: { questions: demoSaved } }),
  )
  await page.route('http://localhost:8000/health', (route) =>
    route.fulfill({
      json: { status: 'ok', service: 'analyst-api', version: '0.1.0', env: 'development' },
    }),
  )
  await page.route('http://localhost:8000/health/ready', (route) =>
    route.fulfill({
      json: { status: 'ok', checks: { database: 'ok', redis: 'ok', storage: 'ok' } },
    }),
  )
}

export async function open(page: Page, path: string, options: MockOptions = {}) {
  await mockApi(page, options)
  await page.goto(path)
  await page.locator('h1').first().waitFor()
  await page.evaluate(() => document.fonts.ready)
}

/** A real set-up answer (QR modules computed by the API for a fixed demo key). */
export const totpSetup = {
  method: 'totp',
  secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
  otpauth_uri:
    'otpauth://totp/Analyst:asha%40urbancart.in?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Analyst',
  qr: [
    '11111110001111101010011001100001101111111',
    '10000010000111100100000110111000101000001',
    '10111010000110001100011010011111001011101',
    '10111010010001100101011100100000001011101',
    '10111010000001011001010101011100001011101',
    '10000010111000000001011011110111101000001',
    '11111110101010101010101010101010101111111',
    '00000000001010010110111110000111100000000',
    '10010110100000101001010111010011010100000',
    '10100100010111110010111010001100010000100',
    '00000111001100101111110001100111100010010',
    '01110101001101011000111001011010101000110',
    '11000111001101101001110011001101011101000',
    '01100000010001010000110100000101100001001',
    '00010010110110001011000111000010111011011',
    '11000101011110011110110101010111100001001',
    '10011110010000001110001001000001110100010',
    '10111001001001101111011100011111001101011',
    '11110111010110000100101000101000101111101',
    '01010101011100111000010100111110010100001',
    '01011010111000100101101011101100011001101',
    '11010001000110110100001010100000111001011',
    '11101011001010110111100000000101010111110',
    '00100001111100000011011101011011100011100',
    '00100011010010100100010010000100011110001',
    '00011100011101100101010011000111011001001',
    '01011110111000110010011111100110001110011',
    '00100100101011010101110011001111001000010',
    '10101010000001000001011110110100110100000',
    '01100101001101011011110001001010001100100',
    '10000011010111111110001011101010011010111',
    '00100100101001011110011110100100111100000',
    '10101010010100001100000100110100111111101',
    '00000000101110000111100111111110100011011',
    '11111110001001101011011000100011101010010',
    '10000010110101001011000111010010100010110',
    '10111010001001110010000001010100111110100',
    '10111010111101111111111010100100001111010',
    '10111010010011001100111100001100100010111',
    '10000010011001001111111011100101110100010',
    '11111110101111100111011110000100101011010',
  ],
  email_hint: null,
}

export const recoveryCodes = [
  'k7m2p-x9q4r',
  'b3n8t-w5j6h',
  'z4c7v-m2k9d',
  'p6q3s-t8y5n',
  'h9w4f-c3r7m',
  'e5u2x-j7b4k',
  'r8d6g-n3p9w',
  'y2t5h-q6m8c',
  'f4k9z-s7v3e',
  'w6b3n-d5x8u',
]

export { session as signedInSession }
