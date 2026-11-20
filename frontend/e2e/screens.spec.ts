import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import {
  analystSession,
  authScreens,
  codeChallenge,
  invitationToken,
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
        await expect(page.getByText('142 of 500 questions used')).toBeVisible()
        await expect(page.getByRole('radio', { name: /Managed AI/ })).toBeChecked()
      }
      if (screen === 'team') {
        await expect(page.getByText('meera@urbancart.in')).toBeVisible()
        await expect(page.getByText('Lata Menon')).toBeVisible()
      }
      if (screen === 'connections') {
        await expect(page.getByText('1 table · 28,540 rows')).toBeVisible()
        await expect(page.getByText('1 of 3 connections')).toBeVisible()
      }
      if (screen === 'dictionary') {
        await expect(page.getByText('We looked at 2 tables and 7 columns.')).toBeVisible()
        await expect(page.getByText('Not confirmed yet')).toBeVisible()
      }
      if (screen === 'ask') {
        await expect(page.getByRole('radio', { name: 'Business' })).toBeChecked()
        await expect(
          page.getByRole('button', { name: /^Were there any unusual days/ }),
        ).toBeVisible()
      }
      if (screen === 'what-if') {
        await expect(page.getByRole('button', { name: 'Run scenario' })).toBeVisible()
      }
      if (screen === 'today') {
        await expect(page.getByText('2 of 4 done')).toBeVisible()
        await expect(page.getByText('Revenue, last 7 days')).toBeVisible()
        await expect(page.getByText('November sales')).toBeVisible()
      }
      if (screen === 'briefings') {
        await expect(page.getByText('Not sent: nothing needed attention')).toBeVisible()
      }
      if (screen === 'tasks') {
        await expect(page.getByText('Fetching Marketing from Google Sheets')).toBeVisible()
      }
      if (screen === 'goals') {
        await expect(page.getByText('₹2.1 L a day is needed from here.')).toBeVisible()
        await expect(page.getByText('1 goal may fall short.', { exact: false })).toBeVisible()
      }
      if (screen === 'dashboard') {
        await expect(page.getByText('₹44.7 L')).toBeVisible()
        await expect(page.getByText('From Ravi Kumar')).toBeVisible()
      }
      if (screen === 'activity') {
        await expect(page.getByText('Turned on two-step verification')).toBeVisible()
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

// M8: a finished answer with its chart, sources and notes, and the "Show my work" drawer.
test.describe('ask: an answer', () => {
  test('matches the baseline', async ({ page }) => {
    await open(page, '/ask/conv1')
    await expect(page.getByRole('article', { name: 'Answer' })).toBeVisible()
    await expect(page.getByRole('figure').locator('svg')).toBeVisible()
    await expect(page).toHaveScreenshot('ask-answer.png', { fullPage: true })
  })

  test('has no accessibility violations', async ({ page }) => {
    await open(page, '/ask/conv1')
    await page.getByRole('figure').locator('svg').waitFor()
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze()
    expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([])
  })

  test('"Show my work" drawer', async ({ page }) => {
    await open(page, '/ask/conv1')
    await page.getByRole('button', { name: 'Show my work' }).click()
    await expect(page.getByRole('dialog', { name: 'How this answer was made' })).toBeVisible()
    await expect(page).toHaveScreenshot('ask-work.png')
  })
})

// M9: a worked-out scenario with its headline, range, assumptions and chart.
test.describe('what-if: a scenario', () => {
  async function run(page: Page) {
    await open(page, '/what-if')
    await page.getByRole('button', { name: 'Run scenario' }).click()
    await expect(page.getByRole('region', { name: /^Prices \+10%/ })).toBeVisible()
    await expect(page.getByRole('figure').locator('svg')).toBeVisible()
  }

  test('matches the baseline', async ({ page }) => {
    await run(page)
    await expect(page.getByText(/Based on shopify_orders/)).toBeVisible()
    await expect(page).toHaveScreenshot('what-if-result.png', { fullPage: true })
  })

  test('has no accessibility violations', async ({ page }) => {
    await run(page)
    await noAxeViolations(page)
  })
})

// M10: a briefing as a report, and the share dialog.
test.describe('report: a briefing', () => {
  test('matches the baseline', async ({ page }) => {
    await open(page, '/reports/rep-briefing')
    await expect(page.getByText('Today’s alerts')).toBeVisible()
    await expect(page).toHaveScreenshot('report-briefing.png', { fullPage: true })
  })

  test('has no accessibility violations', async ({ page }) => {
    await open(page, '/reports/rep-briefing')
    await expect(page.getByText('Today’s alerts')).toBeVisible()
    await noAxeViolations(page)
  })

  test('share dialog', async ({ page }) => {
    await open(page, '/reports/rep-briefing')
    await page.getByRole('button', { name: 'Share' }).click()
    await expect(page.getByRole('checkbox', { name: /Lata/ })).toBeDisabled()
    await noAxeViolations(page)
    await expect(page).toHaveScreenshot('report-share.png')
  })
})

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

// M4: invitations, the team's remove dialog, an Analyst's view and the workspace switcher.
const teamScreens = {
  'accept-invite': (page: Page) =>
    open(page, `/accept-invite#token=${invitationToken}`, { signedIn: false }),
  'team-remove-dialog': async (page: Page) => {
    await open(page, '/team')
    await page.getByRole('button', { name: 'Remove Ravi Kumar' }).click()
    await page.getByRole('dialog', { name: 'Remove Ravi Kumar?' }).waitFor()
  },
  'activity-analyst': async (page: Page) => {
    await open(page, '/activity', {
      refresh: analystSession,
      audit: {
        entries: [
          {
            id: 'a4',
            at: '2026-10-29T13:05:00+00:00',
            action: 'auth.login.success',
            user_id: 'u2',
            user_name: 'Ravi Kumar',
            user_email: 'ravi@urbancart.in',
            target_type: 'session',
            target_id: 's9',
            ip: '198.51.100.7',
            browser: 'Safari on iPhone',
            details: {},
          },
        ],
        next_cursor: null,
        scope: 'own',
      },
    })
    await page.getByText('You see your own activity.', { exact: false }).waitFor()
  },
  'workspace-switcher': async (page: Page) => {
    await open(page, '/today', { refresh: analystSession })
    await page
      .getByRole('button', { name: /switch workspace/ })
      .filter({ visible: true })
      .first()
      .click()
    await page.getByRole('list', { name: 'Your workspaces' }).waitFor()
  },
}

// M6: one connection with its tables (columns and preview open), the access dialog and an Analyst's list.
const connectionScreens = {
  'connection-detail': async (page: Page) => {
    await open(page, '/connections/c1')
    await page.getByRole('button', { name: 'Columns and preview' }).click()
    await page.getByRole('table', { name: /First 4 of 28,540 rows/ }).waitFor()
  },
  'connections-access-dialog': async (page: Page) => {
    await open(page, '/connections')
    await page.getByRole('button', { name: 'Change what Ravi Kumar can see' }).click()
    await page.getByRole('dialog', { name: 'What Ravi Kumar can see' }).waitFor()
    // The button is low on the page; a full-page capture of a scrolled page misplaces the sticky sidebar.
    await page.evaluate(() => window.scrollTo(0, 0))
  },
  // M13: a Zoho Books connection (organisation, data centre, API calls, switching organisation).
  'connection-zoho': async (page: Page) => {
    await open(page, '/connections/c6')
    await page.getByLabel('Read another organisation').waitFor()
  },
  // M14: a database connection (where it is, the write warning, choosing tables).
  'connection-database': async (page: Page) => {
    await open(page, '/connections/c7')
    await page.getByRole('list', { name: 'Tables in the database' }).waitFor()
  },
  'connections-analyst': async (page: Page) => {
    await open(page, '/connections', { refresh: analystSession })
    await page.getByRole('article', { name: 'UrbanCart orders' }).waitFor()
  },
}

for (const [name, reach] of Object.entries({ ...teamScreens, ...connectionScreens })) {
  test.describe(name, () => {
    test('looks right', async ({ page }) => {
      await reach(page)
      await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true })
    })

    test('has no accessibility violations', async ({ page }) => {
      await reach(page)
      await noAxeViolations(page)
    })
  })
}

test('Analysts do not get the Team screen in their menu', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('desktop'), 'desktop sidebar')
  await open(page, '/today', { refresh: analystSession })
  const nav = page.getByTestId('side-nav')
  await expect(nav.getByRole('link', { name: 'Activity' })).toBeVisible()
  await expect(nav.getByRole('link', { name: 'Team' })).toHaveCount(0)
  // M6: Analysts see the connections they may ask about (read-only).
  await expect(nav.getByRole('link', { name: 'Connections' })).toBeVisible()
})
