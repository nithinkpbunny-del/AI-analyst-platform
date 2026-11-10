import { expect, test, type Page } from '@playwright/test'
import { authScreens, open, screens } from './helpers'

// Layout behaviour across the four widths of BUILD_SPEC 11a.9 (run once, in the desktop-light project).
// eslint-disable-next-line no-empty-pattern -- Playwright requires the fixtures argument
test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-light', 'layout checks set their own viewport')
})

const widths = [
  { name: 'phone', width: 375, height: 812 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1280, height: 800 },
  { name: 'wide', width: 1536, height: 900 },
]

async function hasNoHorizontalScroll(page: Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )
}

for (const size of widths) {
  test(`no horizontal scrolling on any screen at ${size.width} px`, async ({ page }) => {
    await page.setViewportSize(size)
    for (const screen of screens) {
      await open(page, `/${screen}`)
      expect(await hasNoHorizontalScroll(page), screen).toBe(true)
    }
    // A connection with its columns and preview open: wide tables scroll inside their own box.
    await open(page, '/connections/c1')
    await page.getByRole('button', { name: 'Columns and preview' }).click()
    await page.getByRole('table', { name: /First 4 of/ }).waitFor()
    expect(await hasNoHorizontalScroll(page), 'connection detail').toBe(true)
    for (const screen of authScreens) {
      await open(page, `/${screen}`, { signedIn: false })
      expect(await hasNoHorizontalScroll(page), screen).toBe(true)
    }
  })
}

test('phone: bottom tabs and floating Ask button, no sidebar', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await open(page, '/today')
  await expect(page.getByTestId('bottom-tabs')).toBeVisible()
  await expect(page.getByTestId('ask-fab')).toBeVisible()
  await expect(page.getByTestId('side-nav')).toBeHidden()
})

test('tablet: icon rail without labels', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 })
  await open(page, '/today')
  const rail = page.getByTestId('side-nav')
  await expect(rail).toBeVisible()
  expect((await rail.boundingBox())?.width).toBe(72)
  await expect(rail.getByText('Dashboard', { exact: true })).toHaveClass(/sr-only/)
  await expect(page.getByTestId('bottom-tabs')).toBeHidden()
})

test('desktop: labelled sidebar that collapses to a rail', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await open(page, '/today')
  const nav = page.getByTestId('side-nav')
  expect((await nav.boundingBox())?.width).toBe(240)
  await expect(nav.getByText('Dashboard', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Collapse sidebar' }).click()
  expect((await nav.boundingBox())?.width).toBe(72)
  await page.reload()
  await page.locator('h1').waitFor()
  expect((await nav.boundingBox())?.width).toBe(72)
})

test('touch targets in the navigation are at least 44 px', async ({ page }) => {
  for (const size of [widths[0]!, widths[2]!]) {
    await page.setViewportSize(size)
    await open(page, '/today')
    const navs = page.locator('nav:visible a, nav:visible button')
    const count = await navs.count()
    for (let i = 0; i < count; i++) {
      const box = await navs.nth(i).boundingBox()
      if (box)
        expect(Math.min(box.height, box.width), `${size.name} control ${i}`).toBeGreaterThanOrEqual(
          44,
        )
    }
  }
})

test('dark mode can be forced from Settings and survives a reload', async ({ page }) => {
  await open(page, '/settings')
  await page.getByRole('radio', { name: 'Dark' }).check()
  await page.reload()
  await page.locator('h1').waitFor()
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe('dark')
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(background).toBe('rgb(18, 17, 42)')
})
