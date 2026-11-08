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

/** Answer API calls with fixed data so screenshots never depend on a running backend. */
export async function mockApi(page: Page) {
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

export async function open(page: Page, path: string) {
  await mockApi(page)
  await page.goto(path)
  await page.locator('h1').first().waitFor()
  await page.evaluate(() => document.fonts.ready)
}
