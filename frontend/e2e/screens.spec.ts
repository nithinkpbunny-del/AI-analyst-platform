import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { authScreens, open, screens } from './helpers'

// BUILD_SPEC 11a.9: visual snapshots of every main screen in light/dark × desktop/mobile (one project each),
// plus an automated WCAG 2.2 AA check of each screen.
for (const screen of screens) {
  test.describe(screen, () => {
    test('looks right', async ({ page }) => {
      await open(page, `/${screen}`)
      if (screen === 'settings') await expect(page.getByText(/API connected/)).toBeVisible()
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
