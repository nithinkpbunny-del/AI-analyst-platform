import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import { demoConversation, demoConversations, demoSaved } from '../../testing/askFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'
import { demoDashboard, demoGoalList, demoGoalOptions, demoTile } from '../../testing/goalsFixtures'
import { demoOrg, demoToday } from '../../testing/reportsFixtures'
import { presetPeriod } from './periods'

type Routes = Parameters<typeof fakeApi>[0]

const viewer = { ...demoSession, role: 'viewer' as const, permissions: ROLE_PERMISSIONS.viewer }

function goalRoutes(extra: Routes = {}) {
  return fakeApi({
    'GET /goals': { status: 200, body: demoGoalList },
    'POST /goals/refresh': { status: 202, body: demoGoalList },
    'GET /goals/options': { status: 200, body: demoGoalOptions },
    ...extra,
  })
}

function dashboardRoutes(extra: Routes = {}) {
  return fakeApi({
    'GET /dashboard': { status: 200, body: demoDashboard },
    'POST /dashboard/refresh': { status: 202, body: demoDashboard },
    ...extra,
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Goals', () => {
  it('shows progress, the projection, the early warning and where the figure came from', async () => {
    const api = goalRoutes()
    renderApp('/goals')
    expect(await screen.findByRole('heading', { level: 1, name: 'Goals' })).toBeVisible()
    const card = await screen.findByRole('article', { name: 'Goal: November sales' })
    expect(within(card).getByText('Behind')).toBeVisible()
    expect(within(card).getByText('₹18.4 L')).toBeVisible()
    expect(within(card).getByText('of ₹50 L')).toBeVisible()
    expect(
      within(card).getByText(/Heading for ₹34.8 L by 30 Nov 2026 \(70% of the target\)/),
    ).toBeVisible()
    expect(within(card).getByText('₹2.1 L a day is needed from here.')).toBeVisible()
    expect(within(card).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '37')
    expect(within(card).getByRole('button', { name: /Source 1: Shopify Orders/ })).toBeVisible()
    expect(within(card).getByText(/last year’s pattern/)).toBeVisible()
    // The early warning names how many goals may fall short.
    expect(screen.getByRole('note')).toHaveTextContent('1 goal may fall short.')
    // An "at most" goal on part of the business, without a name.
    const refunds = screen.getByRole('article', { name: 'Goal: Refunds goal' })
    expect(within(refunds).getByText('of at most ₹1.5 L')).toBeVisible()
    expect(within(refunds).getByText(/Only shipping region = South/)).toBeVisible()
    // Opening the screen asks for progress, without forcing a recomputation.
    await waitFor(() => expect(api.count('POST /goals/refresh')).toBe(1))
    expect(api.body('POST /goals/refresh')).toEqual({ force: false })
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(api.body('POST /goals/refresh')).toEqual({ force: true }))
  })

  it('sets a goal for this month with a filter', async () => {
    const api = goalRoutes({
      'POST /goals': { status: 201, body: demoGoalList.goals[0] },
    })
    renderApp('/goals')
    await userEvent.click(await screen.findByRole('button', { name: 'Set a goal' }))
    const dialog = await screen.findByRole('dialog', { name: 'Set a goal' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save goal' }))
    expect(within(dialog).getByText('Enter a target above zero.')).toBeVisible()
    await userEvent.type(within(dialog).getByLabelText('Target'), '5000000')
    await userEvent.type(within(dialog).getByLabelText('Name (optional)'), 'November sales')
    await userEvent.selectOptions(
      within(dialog).getByLabelText('Only part of the business (optional)'),
      'shipping_region',
    )
    await userEvent.type(within(dialog).getByLabelText('Value'), 'South')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save goal' }))
    await waitFor(() => expect(api.count('POST /goals')).toBe(1))
    expect(api.body('POST /goals')).toEqual({
      metric: 'revenue',
      target: 5000000,
      period_start: '2026-11-01',
      period_end: '2026-11-30',
      direction: 'at_least',
      name: 'November sales',
      filters: { shipping_region: 'South' },
    })
    expect(await screen.findByText('Goal saved. Checking its progress now.')).toBeVisible()
    await waitFor(() => expect(api.body('POST /goals/refresh')).toEqual({ force: true }))
  })

  it('a metric where lower is better starts as an "at most" goal; server errors are shown', async () => {
    const api = goalRoutes({
      'POST /goals': apiError(
        422,
        'unknown_metric',
        'Choose a metric from your business dictionary.',
      ),
    })
    renderApp('/goals')
    await userEvent.click(await screen.findByRole('button', { name: 'Set a goal' }))
    const dialog = await screen.findByRole('dialog', { name: 'Set a goal' })
    await userEvent.selectOptions(within(dialog).getByLabelText('Metric'), 'refunds')
    expect(within(dialog).getByLabelText('Kind of goal')).toHaveValue('at_most')
    await userEvent.selectOptions(within(dialog).getByLabelText('Period'), 'this_quarter')
    expect(within(dialog).getByLabelText('From')).toHaveValue('2026-10-01')
    expect(within(dialog).getByLabelText('To')).toHaveValue('2026-12-31')
    await userEvent.type(within(dialog).getByLabelText('Target'), '150000')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save goal' }))
    expect(
      await within(dialog).findByText('Choose a metric from your business dictionary.'),
    ).toBeVisible()
    expect(api.body('POST /goals')).toMatchObject({ metric: 'refunds', direction: 'at_most' })
  })

  it('edits and removes a goal', async () => {
    const api = goalRoutes({
      'PATCH /goals/goal-nov': { status: 200, body: demoGoalList.goals[0] },
      'DELETE /goals/goal-nov': { status: 204 },
    })
    renderApp('/goals')
    const card = await screen.findByRole('article', { name: 'Goal: November sales' })
    await userEvent.click(within(card).getByRole('button', { name: 'Edit: November sales' }))
    const dialog = await screen.findByRole('dialog', { name: 'Change goal' })
    expect(within(dialog).getByLabelText('Metric')).toBeDisabled()
    const target = within(dialog).getByLabelText('Target')
    await userEvent.clear(target)
    await userEvent.type(target, '4500000')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save goal' }))
    await waitFor(() =>
      expect(api.body('PATCH /goals/goal-nov')).toMatchObject({ target: 4500000 }),
    )
    await userEvent.click(within(card).getByRole('button', { name: 'Remove: November sales' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Remove goal' }))
    await waitFor(() => expect(api.count('DELETE /goals/goal-nov')).toBe(1))
  })

  it('viewers look but cannot set or change goals', async () => {
    const api = goalRoutes()
    renderApp('/goals', { session: viewer })
    expect(await screen.findByRole('article', { name: 'Goal: November sales' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Set a goal' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Edit/ })).toBeNull()
    expect(api.count('GET /goals/options')).toBe(0)
  })

  it('says what to do when there is no dictionary yet', async () => {
    goalRoutes({
      'GET /goals': { status: 200, body: { ...demoGoalList, goals: [] } },
      'POST /goals/refresh': { status: 202, body: { ...demoGoalList, goals: [] } },
      'GET /goals/options': { status: 200, body: { ...demoGoalOptions, metrics: [] } },
    })
    renderApp('/goals')
    expect(await screen.findByText(/Connect data and confirm the dictionary first/)).toBeVisible()
  })

  it('preset periods are calendar months and quarters', () => {
    expect(presetPeriod('this_month', '2026-02-10')).toEqual({
      start: '2026-02-01',
      end: '2026-02-28',
    })
    expect(presetPeriod('next_month', '2026-12-10')).toEqual({
      start: '2027-01-01',
      end: '2027-01-31',
    })
    expect(presetPeriod('this_quarter', '2026-05-10')).toEqual({
      start: '2026-04-01',
      end: '2026-06-30',
    })
  })
})

describe('Dashboard', () => {
  it('shows live tiles with their key figure and sources, errors in words, and the team’s tiles', async () => {
    const api = dashboardRoutes()
    renderApp('/dashboard')
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible()
    const tile = await screen.findByRole('article', { name: 'Tile: Revenue last month' })
    expect(within(tile).getByText('₹44.7 L')).toBeVisible()
    expect(within(tile).getByText(/12.4% vs the period before/)).toBeVisible()
    expect(
      within(tile).getAllByRole('button', { name: /Source 1: Shopify Orders/ })[0],
    ).toBeVisible()
    expect(
      within(tile).getByRole('table', { name: 'Figures for Revenue last month' }),
    ).toBeVisible()
    expect(within(tile).getByRole('link', { name: /Open the answer/ })).toHaveAttribute(
      'href',
      '/ask/conv-1',
    )
    const outdated = screen.getByRole('article', { name: 'Tile: Orders by channel' })
    expect(within(outdated).getByText(/no longer matches your business dictionary/)).toBeVisible()
    const team = screen.getByRole('region', { name: 'From your team' })
    expect(within(team).getByText('From Ravi Kumar')).toBeVisible()
    expect(within(team).getByText('Not run for you yet.')).toBeVisible()
    expect(within(team).queryByRole('button', { name: /Options for/ })).toBeNull()
    await waitFor(() => expect(api.body('POST /dashboard/refresh')).toEqual({ force: false }))
    await userEvent.click(screen.getByRole('button', { name: 'Refresh all' }))
    await waitFor(() => expect(api.body('POST /dashboard/refresh')).toEqual({ force: true }))
  })

  it('moves a tile with its menu and says so', async () => {
    const api = dashboardRoutes({ 'PUT /dashboard/order': { status: 200, body: demoDashboard } })
    renderApp('/dashboard')
    const tile = await screen.findByRole('article', { name: 'Tile: Orders by channel' })
    await userEvent.click(
      within(tile).getByRole('button', { name: 'Options for Orders by channel' }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Move up' }))
    await waitFor(() =>
      expect(api.body('PUT /dashboard/order')).toEqual({ tile_ids: ['tile-2', 'tile-1'] }),
    )
    expect(screen.getByText('Orders by channel is now number 1.')).toBeInTheDocument()
  })

  it('changes a tile’s settings and shares it with the team', async () => {
    const api = dashboardRoutes({
      'PATCH /dashboard/tiles/tile-1': { status: 200, body: demoTile({ shared: true }) },
    })
    renderApp('/dashboard')
    const tile = await screen.findByRole('article', { name: 'Tile: Revenue last month' })
    await userEvent.click(
      within(tile).getByRole('button', { name: 'Options for Revenue last month' }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Tile settings' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tile settings' })
    await userEvent.selectOptions(within(dialog).getByLabelText('Refresh every'), '240')
    await userEvent.click(within(dialog).getByLabelText('Keep these dates'))
    await userEvent.click(within(dialog).getByLabelText('Show on everyone’s dashboard'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save tile' }))
    await waitFor(() =>
      expect(api.body('PATCH /dashboard/tiles/tile-1')).toEqual({
        title: 'Revenue last month',
        refresh_minutes: 240,
        dates: 'fixed',
        shared: true,
      }),
    )
    expect(await screen.findByText('Tile saved.')).toBeVisible()
  })

  it('removes a tile after asking', async () => {
    const api = dashboardRoutes({ 'DELETE /dashboard/tiles/tile-1': { status: 204 } })
    renderApp('/dashboard')
    const tile = await screen.findByRole('article', { name: 'Tile: Revenue last month' })
    await userEvent.click(
      within(tile).getByRole('button', { name: 'Options for Revenue last month' }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Remove from dashboard' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Remove tile' }))
    await waitFor(() => expect(api.count('DELETE /dashboard/tiles/tile-1')).toBe(1))
  })

  it('viewers see only what is shared, and how to get more', async () => {
    dashboardRoutes({
      'GET /dashboard': { status: 200, body: { ...demoDashboard, tiles: [], can_edit: false } },
      'POST /dashboard/refresh': {
        status: 202,
        body: { ...demoDashboard, tiles: [], can_edit: false },
      },
    })
    renderApp('/dashboard', { session: viewer })
    const message = await screen.findByText(/Nothing is on the dashboard yet/)
    expect(message).toBeVisible()
    expect(within(message.parentElement!).queryByRole('link')).toBeNull()
  })
})

describe('Pin to dashboard', () => {
  it('pins an answer from Ask with rolling dates', async () => {
    const api = fakeApi({
      'GET /conversations': { status: 200, body: { conversations: demoConversations } },
      'GET /saved-questions': { status: 200, body: { questions: demoSaved } },
      'GET /conversations/conv1': { status: 200, body: demoConversation },
      'POST /dashboard/tiles': { status: 201, body: demoTile() },
    })
    renderApp('/ask/conv1')
    await userEvent.click(await screen.findByRole('button', { name: 'Pin' }))
    const dialog = await screen.findByRole('dialog', { name: 'Pin to dashboard' })
    expect(within(dialog).getByLabelText('Title')).toHaveValue(
      demoConversation.messages[0]!.content,
    )
    expect(within(dialog).getByLabelText('Move the dates forward')).toBeChecked()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Pin to dashboard' }))
    await waitFor(() =>
      expect(api.body('POST /dashboard/tiles')).toEqual({
        message_id: 'm2',
        title: demoConversation.messages[0]!.content,
        dates: 'rolling',
      }),
    )
    expect(await within(dialog).findByText('Pinned to your dashboard.')).toBeVisible()
    expect(within(dialog).getByRole('link', { name: 'Open dashboard' })).toHaveAttribute(
      'href',
      '/dashboard',
    )
  })

  it('viewers have no Pin button', async () => {
    fakeApi({
      'GET /conversations/conv1': { status: 200, body: demoConversation },
    })
    renderApp('/ask/conv1', { session: viewer })
    expect(await screen.findByRole('heading', { level: 1 })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Pin' })).toBeNull()
  })
})

describe('Today', () => {
  it('shows running goals with their status and a link to all goals', async () => {
    fakeApi({
      'GET /today': { status: 200, body: demoToday },
      'GET /org': { status: 200, body: demoOrg() },
    })
    renderApp('/today')
    const goals = await screen.findByRole('region', { name: 'Goals' })
    expect(within(goals).getByText('November sales')).toBeVisible()
    expect(within(goals).getByText('Behind')).toBeVisible()
    expect(within(goals).getByText('₹18.4 L of ₹50 L, heading for ₹34.8 L')).toBeVisible()
    expect(within(goals).getByRole('link', { name: 'See all goals' })).toHaveAttribute(
      'href',
      '/goals',
    )
  })
})
