import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import { demoConversations, demoSaved } from '../../testing/askFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'
import { demoOrg, demoToday } from '../../testing/reportsFixtures'
import { demoTemplates } from '../../testing/templatesFixtures'

type Routes = Parameters<typeof fakeApi>[0]

const noIndustryToday = {
  ...demoToday,
  checklist: { company: false, connect: false, dictionary: false, ask: false, done: false },
}

function routes(extra: Routes = {}) {
  return fakeApi({
    'GET /org/templates': { status: 200, body: demoTemplates },
    'GET /org': { status: 200, body: demoOrg({ industry: null }) },
    'GET /today': { status: 200, body: noIndustryToday },
    'GET /conversations': { status: 200, body: { conversations: demoConversations } },
    'GET /saved-questions': { status: 200, body: { questions: demoSaved } },
    ...extra,
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Choosing the kind of business at onboarding', () => {
  it('an Owner opens the picker from the checklist, sees what a template sets up, and saves', async () => {
    const api = routes({
      'PATCH /org': { status: 200, body: demoOrg({ industry: 'restaurant' }) },
    })
    renderApp('/today')
    const checklist = await screen.findByRole('region', { name: 'Get to your first insight' })
    await userEvent.click(within(checklist).getByRole('button', { name: /Open: Company details/ }))
    const dialog = await screen.findByRole('dialog', { name: 'What kind of business is it?' })
    expect(await within(dialog).findByRole('radio', { name: 'Retail' })).toBeVisible()
    expect(within(dialog).getByRole('button', { name: 'Use this template' })).toBeDisabled()
    await userEvent.click(within(dialog).getByRole('radio', { name: 'Restaurant' }))
    const preview = within(dialog).getByRole('region', {
      name: 'What this template sets up: Restaurant',
    })
    expect(within(preview).getByText('Food cost')).toBeVisible()
    expect(
      within(preview).getByText('Is food cost rising, and which menu items push it up?'),
    ).toBeVisible()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use this template' }))
    await waitFor(() => expect(api.body('PATCH /org')).toEqual({ industry: 'restaurant' }))
    expect(
      await screen.findByText('Template chosen. The suggestions now fit your business.'),
    ).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // The checklist is asked for again, so the step can tick.
    await waitFor(() => expect(api.count('GET /today')).toBeGreaterThan(1))
  })

  it('says so when the templates cannot be loaded', async () => {
    routes({
      'GET /org/templates': apiError(500, 'server_error', 'Something went wrong.'),
    })
    renderApp('/today')
    const checklist = await screen.findByRole('region', { name: 'Get to your first insight' })
    await userEvent.click(within(checklist).getByRole('button', { name: /Open: Company details/ }))
    const dialog = await screen.findByRole('dialog')
    // (the query retries a failed request once first)
    expect(
      await within(dialog).findByText(/templates could not be loaded/, {}, { timeout: 3000 }),
    ).toBeVisible()
  })

  it('shows the error when the choice cannot be saved', async () => {
    routes({
      'PATCH /org': apiError(403, 'forbidden', 'Only Owners can change workspace settings.'),
    })
    renderApp('/today')
    await userEvent.click(await screen.findByRole('button', { name: /Open: Company details/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('radio', { name: 'Retail' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use this template' }))
    expect(await within(dialog).findByText(/Only Owners can change/)).toBeVisible()
    expect(dialog).toBeVisible()
  })
})

describe('Settings', () => {
  it('previews the template of the chosen kind of business, also before it is saved', async () => {
    routes({ 'GET /org': { status: 200, body: demoOrg({ industry: 'retail' }) } })
    renderApp('/settings')
    const preview = await screen.findByRole('region', {
      name: 'What this template sets up: Retail',
    })
    expect(within(preview).getByText('Bills')).toBeVisible()
    await userEvent.selectOptions(screen.getByLabelText('Kind of business'), 'restaurant')
    expect(
      await screen.findByRole('region', { name: 'What this template sets up: Restaurant' }),
    ).toBeVisible()
  })
})

describe('Ask starters', () => {
  it('use the template questions once the kind of business is chosen', async () => {
    const api = routes({
      'GET /org': { status: 200, body: demoOrg({ industry: 'retail' }) },
      'POST /conversations': {
        status: 500,
        body: { error: { code: 'x', message: 'stop here' } },
      },
    })
    renderApp('/ask')
    const first = await screen.findByRole('button', {
      name: 'Which store or category drove the change in sales last month?',
    })
    expect(
      screen.getAllByRole('button').filter((b) => b.className.includes('rounded-pill')).length,
    ).toBeGreaterThanOrEqual(4)
    expect(
      screen.queryByRole('button', { name: 'Which marketing channel gives the best return?' }),
    ).toBeNull()
    await userEvent.click(first)
    await waitFor(() => expect(api.count('POST /conversations')).toBe(1))
    expect(api.body('POST /conversations')).toEqual({
      question: 'Which store or category drove the change in sales last month?',
      lens: 'business',
    })
  })

  it('stay general without a template, for "something else", and when templates fail to load', async () => {
    for (const org of [demoOrg({ industry: null }), demoOrg({ industry: 'other' })]) {
      routes({ 'GET /org': { status: 200, body: org } })
      const view = renderApp('/ask')
      expect(
        await screen.findByRole('button', {
          name: 'Which marketing channel gives the best return?',
        }),
      ).toBeVisible()
      view.unmount()
      vi.unstubAllGlobals()
    }
    routes({
      'GET /org': { status: 200, body: demoOrg({ industry: 'retail' }) },
      'GET /org/templates': apiError(500, 'server_error', 'Something went wrong.'),
    })
    renderApp('/ask')
    expect(
      await screen.findByRole('button', { name: 'Which marketing channel gives the best return?' }),
    ).toBeVisible()
  })
})

describe('Non-owners', () => {
  it('do not get the onboarding checklist, so no picker', async () => {
    routes()
    renderApp('/today', {
      session: { ...demoSession, role: 'analyst', permissions: ROLE_PERMISSIONS.analyst },
    })
    await screen.findByRole('heading', { name: 'Today', level: 1 })
    expect(screen.queryByRole('region', { name: 'Get to your first insight' })).toBeNull()
  })
})
