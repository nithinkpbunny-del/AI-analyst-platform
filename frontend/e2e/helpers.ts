import type { Page } from '@playwright/test'

export const screens = [
  'today',
  'ask',
  'dashboard',
  'what-if',
  'goals',
  'briefings',
  'tasks',
  'connections',
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
] as const

const API = 'http://localhost:8000'

/** The UrbanCart demo Owner (same as src/lib/demoSession.ts) as /auth/refresh returns it. */
const session = {
  user: { id: 'u1', email: 'asha@urbancart.in', name: 'Asha Rao', preferred_language: 'en' },
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
  access_token: 'e2e-access-token',
  token_type: 'bearer',
  expires_in: 600,
  csrf_token: 'e2e-csrf',
}

/** Answer API calls with fixed data so screenshots never depend on a running backend. */
export async function mockApi(page: Page, { signedIn = true }: { signedIn?: boolean } = {}) {
  await page.route(`${API}/auth/csrf`, (route) =>
    route.fulfill({ json: { csrf_token: 'e2e-csrf' } }),
  )
  await page.route(`${API}/auth/refresh`, (route) =>
    signedIn
      ? route.fulfill({ json: session })
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

export async function open(page: Page, path: string, options: { signedIn?: boolean } = {}) {
  await mockApi(page, options)
  await page.goto(path)
  await page.locator('h1').first().waitFor()
  await page.evaluate(() => document.fonts.ready)
}
