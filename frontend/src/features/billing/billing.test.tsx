import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import { demoAiSettings, demoUsage } from '../../testing/aiFixtures'
import {
  demoBilling,
  demoSummary,
  previewAllowed,
  previewBlocked,
} from '../../testing/billingFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'
import { demoOrg, demoToday } from '../../testing/reportsFixtures'

type Routes = Parameters<typeof fakeApi>[0]

function routes(extra: Routes = {}) {
  return fakeApi({
    'GET /org/billing': { status: 200, body: demoBilling },
    'GET /org/billing/summary': { status: 200, body: demoSummary },
    'GET /org/ai': { status: 200, body: demoAiSettings },
    'GET /org/usage': { status: 200, body: demoUsage },
    'GET /org': { status: 200, body: demoOrg() },
    ...extra,
  })
}

async function section() {
  return within(
    (await screen.findByRole('heading', { name: 'Plan and billing' })).closest(
      'section',
    ) as HTMLElement,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Plan and billing in Settings (Owner)', () => {
  it('says plainly that nothing is charged, shows the plans without a price and the 80% warning', async () => {
    routes()
    renderApp('/settings')
    const billing = await section()
    expect(billing.getByText(/Billing is not switched on yet/)).toBeVisible()
    expect(await billing.findByText('410 of 500 questions used')).toBeVisible()
    expect(billing.getByRole('meter', { name: 'Questions used this month' })).toHaveAttribute(
      'aria-valuenow',
      '410',
    )
    expect(billing.getByRole('status')).toHaveTextContent(/82% of this month’s questions are used/)
    const managed = billing.getByRole('article', { name: 'Managed AI' })
    expect(within(managed).getByText('Current plan')).toBeVisible()
    expect(within(managed).getByText('Price: coming soon')).toBeVisible()
    expect(within(managed).getByText('500 questions and long analyses a month')).toBeVisible()
    const own = billing.getByRole('article', { name: 'Your own AI key' })
    expect(within(own).getByText('No platform question limit')).toBeVisible()
    expect(within(own).queryByText('Current plan')).toBeNull()
    expect(billing.queryByText(/₹\d/, { selector: 'p' })).toBeNull() // no price anywhere
  })

  it('says when the limit is reached, and when there is no limit', async () => {
    routes({
      'GET /org/billing': {
        status: 200,
        body: {
          ...demoBilling,
          limit: { ...demoBilling.limit, used: 500, percent: 100, level: 'reached' },
        },
      },
    })
    const view = renderApp('/settings')
    expect((await (await section()).findByRole('status')).textContent).toMatch(
      /All questions for this month are used\. New questions are paused until 1 Dec 2026/,
    )
    view.unmount()
    vi.unstubAllGlobals()
    routes({
      'GET /org/billing': {
        status: 200,
        body: {
          ...demoBilling,
          plan: 'byok',
          limit: { ...demoBilling.limit, plan: 'byok', limit: null, percent: null, level: 'none' },
        },
      },
    })
    renderApp('/settings')
    expect(
      await (await section()).findByText('No monthly limit: this workspace uses its own AI key.'),
    ).toBeVisible()
  })

  it('shows the monthly summary as invoice lines with no amount due, and downloads it as CSV', async () => {
    const api = routes({
      'GET /org/billing/summary.csv': { status: 200, body: undefined },
    })
    renderApp('/settings')
    const billing = await section()
    const table = await billing.findByRole('table', { name: 'Monthly usage summary' })
    expect(
      within(table).getByText('Questions and long analyses included in the plan'),
    ).toBeVisible()
    expect(within(table).getByText('410')).toBeVisible()
    expect(within(table).getByText('$1.35')).toBeVisible()
    expect(within(table).getByText('₹0 (no price is set yet)')).toBeVisible()
    expect(
      billing.getByText(
        'Draft: the month is not over yet, so these numbers can still change. Plan: Managed AI.',
      ),
    ).toBeVisible()
    expect(billing.getByText('Your own AI key to Managed AI on 2 Nov 2026')).toBeVisible()
    await userEvent.selectOptions(billing.getByLabelText('Month'), '2026-10')
    await waitFor(() =>
      expect(api.calls.some((c) => c.url.includes('/org/billing/summary?month=2026-10'))).toBe(
        true,
      ),
    )
    // The CSV is the same month, fetched through the signed-in download.
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:summary'), revokeObjectURL: vi.fn() })
    await userEvent.click(billing.getByRole('button', { name: 'Download CSV' }))
    await waitFor(() =>
      expect(api.calls.some((c) => c.url.includes('/org/billing/summary.csv?month=2026-10'))).toBe(
        true,
      ),
    )
  })

  it.each([
    [
      { in_progress: false, frozen_at: null },
      'This month has ended. Its final summary is saved within a few minutes. Plan: Managed AI.',
    ],
    [
      { in_progress: false, frozen_at: '2026-12-01T00:10:00+05:30' },
      'Final summary, saved on 1 Dec 2026. Plan: Managed AI.',
    ],
  ])('says whether a month is final (%o)', async (state, words) => {
    routes({ 'GET /org/billing/summary': { status: 200, body: { ...demoSummary, ...state } } })
    renderApp('/settings')
    const billing = await section()
    expect(await billing.findByText(words)).toBeVisible()
  })

  it('lists what billing recorded in plain words and says nothing was sent', async () => {
    routes()
    renderApp('/settings')
    const billing = await section()
    expect(
      await billing.findByText('80% of the monthly questions used (November 2026)'),
    ).toBeVisible()
    expect(billing.getByText('Plan changed: Your own AI key → Managed AI')).toBeVisible()
    expect(billing.getByText(/Nothing was sent to a payment provider/)).toBeVisible()
  })

  it('a blocked plan change says why and cannot be confirmed', async () => {
    const api = routes({
      'POST /org/billing/plan-change/preview': { status: 200, body: previewBlocked },
    })
    renderApp('/settings')
    const billing = await section()
    await userEvent.click(await billing.findByRole('button', { name: /Switch to this plan/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Switch to Your own AI key' })
    expect(await within(dialog).findByText('This change cannot be made yet')).toBeVisible()
    expect(within(dialog).getByText(/Add your own AI key first/)).toBeVisible()
    expect(
      within(dialog).getByText('Nothing is charged: billing is not switched on.'),
    ).toBeVisible()
    expect(within(dialog).getByRole('button', { name: 'Switch to Your own AI key' })).toBeDisabled()
    expect(api.body('POST /org/billing/plan-change/preview')).toEqual({ plan: 'byok' })
    expect(api.count('PUT /org/billing/plan')).toBe(0)
  })

  it('previews what changes, then confirms', async () => {
    const api = routes({
      'POST /org/billing/plan-change/preview': { status: 200, body: previewAllowed },
      'PUT /org/billing/plan': {
        status: 200,
        body: {
          ...demoBilling,
          plan: 'byok',
          limit: { ...demoBilling.limit, plan: 'byok', limit: null, percent: null, level: 'none' },
        },
      },
    })
    renderApp('/settings')
    const billing = await section()
    await userEvent.click(await billing.findByRole('button', { name: /Switch to this plan/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Switch to Your own AI key' })
    expect(
      await within(dialog).findByText('Questions will use your gemini key ending in 0000.'),
    ).toBeVisible()
    expect(within(dialog).getByText('The change takes effect at once.')).toBeVisible()
    expect(api.count('PUT /org/billing/plan')).toBe(0) // reading is not changing
    await userEvent.click(within(dialog).getByRole('button', { name: 'Switch to Your own AI key' }))
    await waitFor(() => expect(api.body('PUT /org/billing/plan')).toEqual({ plan: 'byok' }))
    expect(await billing.findByText('Your plan is now Your own AI key.')).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('shows the server’s reason when the confirm is refused', async () => {
    routes({
      'POST /org/billing/plan-change/preview': { status: 200, body: previewAllowed },
      'PUT /org/billing/plan': apiError(
        409,
        'key_missing',
        'Add your own AI key first, then switch to it.',
      ),
    })
    renderApp('/settings')
    await userEvent.click(
      await (await section()).findByRole('button', { name: /Switch to this plan/ }),
    )
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(
      await within(dialog).findByRole('button', { name: 'Switch to Your own AI key' }),
    )
    expect(
      await within(dialog).findByText('Add your own AI key first, then switch to it.'),
    ).toBeVisible()
  })

  it('says when billing cannot be loaded', async () => {
    routes({ 'GET /org/billing': apiError(500, 'server_error', 'Something went wrong.') })
    renderApp('/settings')
    expect(
      await within(
        (await screen.findByRole('heading', { name: 'Plan and billing' })).closest(
          'section',
        ) as HTMLElement,
      ).findByText(/could not be loaded/, {}, { timeout: 3000 }),
    ).toBeVisible()
  }, 10_000)
})

describe('Other roles', () => {
  it.each(['analyst', 'viewer'] as const)(
    '%ss do not get the section or ask for billing',
    async (role) => {
      const api = routes()
      renderApp('/settings', {
        session: { ...demoSession, role, permissions: ROLE_PERMISSIONS[role] },
      })
      await screen.findByRole('heading', { name: 'Settings', level: 1 })
      expect(screen.queryByRole('heading', { name: 'Plan and billing' })).toBeNull()
      expect(api.count('GET /org/billing')).toBe(0)
    },
  )
})

describe('Today: the usage notice', () => {
  const owner = (usage_warning: unknown) =>
    routes({ 'GET /today': { status: 200, body: { ...demoToday, usage_warning } } })

  it('tells an Owner at 80% and when the limit is used up, with a link to plan and usage', async () => {
    owner({ level: 'warning', used: 410, limit: 500, percent: 82, resets_on: '2026-12-01' })
    const view = renderApp('/today')
    expect(
      await screen.findByText(/82% of this month’s questions are used \(410 of 500\)/),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'See plan and usage' })).toHaveAttribute(
      'href',
      '/settings',
    )
    view.unmount()
    vi.unstubAllGlobals()
    owner({ level: 'reached', used: 500, limit: 500, percent: 100, resets_on: '2026-12-01' })
    renderApp('/today')
    expect(
      await screen.findByText(
        /All 500 questions for this month are used\. They reset on 1 Dec 2026\./,
      ),
    ).toBeVisible()
  })

  it('shows nothing when there is no warning', async () => {
    owner(null)
    renderApp('/today')
    await screen.findByRole('heading', { name: 'Today', level: 1 })
    await screen.findByText('This morning’s briefing')
    expect(screen.queryByText('See plan and usage')).toBeNull()
  })
})
