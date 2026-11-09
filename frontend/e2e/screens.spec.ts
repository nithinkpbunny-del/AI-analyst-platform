import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import {
  authScreens,
  codeChallenge,
  open,
  recoveryCodes,
  screens,
  sessionWithout2fa,
  signedInSession,
  totpSetup,
} from './helpers'

async function noAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([])
}

// BUILD_SPEC 11a.9: visual snapshots of every main screen in light/dark × desktop/mobile (one project each),
// plus an automated WCAG 2.2 AA check of each screen.
for (const screen of screens) {
  test.describe(screen, () => {
    test('looks right', async ({ page }) => {
      await open(page, `/${screen}`)
      if (screen === 'settings') {
        await expect(page.getByText(/API connected/)).toBeVisible()
        await expect(page.getByText('Safari on iPhone')).toBeVisible()
      }
      await expect(page).toHaveScreenshot(`${screen}.png`, { fullPage: true })
    })

    test('has no accessibility violations', async ({ page }) => {
      await open(page, `/${screen}`)
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze()
      expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([])
    })
  })
}

test('the "More" sheet on phones', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'), 'phones only')
  await open(page, '/today')
  await page.getByTestId('bottom-tabs').getByRole('button', { name: 'More' }).click()
  await expect(page.getByRole('dialog', { name: 'More' })).toBeVisible()
  await expect(page).toHaveScreenshot('more-sheet.png')
})

// Signed-out screens: same visual and accessibility checks. verify-email and reset-password are opened without
// a link token, so they show their "ask for a new link" state (deterministic, no backend needed).
for (const screen of authScreens) {
  test.describe(`auth: ${screen}`, () => {
    test('looks right', async ({ page }) => {
      await open(page, `/${screen}`, { signedIn: false })
      if (screen === 'signin' || screen === 'signup') {
        await expect(page.getByRole('link', { name: 'Continue with Google' })).toBeVisible()
      }
      await expect(page).toHaveScreenshot(`auth-${screen}.png`, { fullPage: true })
    })

    test('has no accessibility violations', async ({ page }) => {
      await open(page, `/${screen}`, { signedIn: false })
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze()
      expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([])
    })
  })
}

test('signed-out visitors are sent to sign in', async ({ page }) => {
  await open(page, '/today', { signedIn: false })
  await expect(page).toHaveURL(/\/signin$/)
})

// M3: the second step of signing in. The code step and the Owner set-up are reached through a mocked sign-in.
async function signInTo(page: Page, answer: object) {
  await open(page, '/signin', { signedIn: false })
  await page.route('http://localhost:8000/auth/login', (route) => route.fulfill({ json: answer }))
  await page.getByLabel('Email').fill('asha@urbancart.in')
  await page.getByLabel('Password', { exact: true }).fill('ledger-haldi-neel-42')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.locator('h1').first().waitFor()
}

const setupChallenge = { ...codeChallenge, status: 'two_factor_setup_required', method: null }

async function scanStep(page: Page) {
  await page.route('http://localhost:8000/auth/login/setup', (route) =>
    route.fulfill({ json: totpSetup }),
  )
  await signInTo(page, setupChallenge)
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('img', { name: 'QR code for your authenticator app' }).waitFor()
}

const twoStepScreens = {
  'two-step-code': (page: Page) => signInTo(page, codeChallenge),
  'two-step-setup': (page: Page) => signInTo(page, setupChallenge),
  'two-step-scan': scanStep,
  'two-step-recovery': async (page: Page) => {
    await page.route('http://localhost:8000/auth/login/setup/confirm', (route) =>
      route.fulfill({ json: { ...signedInSession, recovery_codes: recoveryCodes } }),
    )
    await scanStep(page)
    await page.getByLabel('6-digit code').fill('123456')
    await page.getByRole('button', { name: 'Turn on two-step verification' }).click()
    await page.getByRole('list', { name: 'Recovery codes' }).waitFor()
  },
  'two-step-required': (page: Page) =>
    open(page, '/setup-two-factor', { refresh: sessionWithout2fa }),
}

for (const [name, reach] of Object.entries(twoStepScreens)) {
  test.describe(`auth: ${name}`, () => {
    test('looks right', async ({ page }) => {
      await reach(page)
      await expect(page).toHaveScreenshot(`auth-${name}.png`, { fullPage: true })
    })

    test('has no accessibility violations', async ({ page }) => {
      await reach(page)
      await noAxeViolations(page)
    })
  })
}

test('an Owner without two-step verification is sent to set it up', async ({ page }) => {
  await open(page, '/today', { refresh: sessionWithout2fa })
  await expect(page).toHaveURL(/\/setup-two-factor$/)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Turn on two-step verification')
})
