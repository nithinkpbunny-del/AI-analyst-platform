import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import { apiError, fakeApi } from '../../testing/fakeApi'
import { demoFacts, demoOptions, demoResult } from '../../testing/whatIfFixtures'

type Routes = Parameters<typeof fakeApi>[0]

function routes(extra: Routes = {}) {
  return fakeApi({
    'GET /whatif/options': { status: 200, body: demoOptions },
    'GET /whatif/values': (_init, url) => ({
      status: 200,
      body: {
        values:
          url.searchParams.get('column') === 'channel'
            ? ['Instagram', 'YouTube']
            : ['Electronics', 'Fashion'],
      },
    }),
    'POST /whatif/run': { status: 200, body: demoResult() },
    ...extra,
  })
}

const viewer = { ...demoSession, role: 'viewer' as const, permissions: ROLE_PERMISSIONS.viewer }

afterEach(() => {
  vi.unstubAllGlobals()
})

async function runDefault() {
  await userEvent.click(await screen.findByRole('button', { name: 'Run scenario' }))
  return await screen.findByRole('region', { name: /^Prices \+10%/ })
}

describe('What-if screen', () => {
  it('runs a scenario with the chosen lever, scope and horizon, and sends no AI request', async () => {
    const api = routes()
    renderApp('/what-if')
    expect(await screen.findByRole('heading', { name: 'What-if', level: 1 })).toBeVisible()
    await userEvent.selectOptions(await screen.findByLabelText('Where'), 'product_type')
    await userEvent.selectOptions(await screen.findByLabelText('Which one'), 'Electronics')
    await userEvent.selectOptions(screen.getByLabelText('For how long'), '6')
    await userEvent.click(screen.getByRole('button', { name: 'Run scenario' }))
    await screen.findByRole('region', { name: /^Prices \+10%/ })
    expect(api.body('POST /whatif/run')).toEqual({
      lever: 'price',
      change: 10,
      metric: 'revenue',
      scope: { product_type: 'Electronics' },
      horizon: 6,
    })
    expect(api.calls.some((c) => c.key.includes('/conversations'))).toBe(false)
  })

  it('always shows the range with the expected change, never one without the other', async () => {
    routes()
    renderApp('/what-if')
    await runDefault()
    expect(screen.getByText(/Expected change over the next 3 months:/)).toBeVisible()
    expect(screen.getByText('−₹2.4 L')).toBeVisible()
    expect(screen.getByText(/Likely between −₹6\.4 L and \+₹1\.9 L \(80% range\)/)).toBeVisible()
    // The range includes no change, and the screen says so.
    expect(screen.getByText(/direction is not clear/)).toBeVisible()
    const card = screen.getByRole('region', { name: /^Prices \+10%/ })
    expect(within(card).getByText(/Likely between ₹53 L and ₹69 L/)).toBeVisible()
  })

  it('says which assumptions are a typical figure and why, and the confidence', async () => {
    routes()
    renderApp('/what-if')
    const card = await runDefault()
    expect(card).toHaveTextContent('A typical figure is used')
    expect(card).toHaveTextContent('Your history was too mixed to give a clear answer.')
    expect(card).toHaveTextContent('Low confidence')
    expect(card).toHaveTextContent('Nothing else changes')
    expect(screen.getByRole('note')).toHaveTextContent('typical figure for your kind of business')
  })

  it('names the weeks that informed an estimate learned from history', async () => {
    const facts = demoFacts({
      assumptions: [
        {
          key: 'price_elasticity',
          value: -0.56,
          low: -0.9,
          high: -0.2,
          source: 'history',
          editable: true,
          t_value: 2.5,
        },
      ],
      confidence: { level: 'medium', reason: 'some_history' },
      informed_by: { source: 'history', weeks: 91, start: '2024-12-30', end: '2026-09-21' },
    })
    routes({
      'POST /whatif/run': {
        status: 200,
        body: demoResult({ facts, notes: ['what_if_assumptions'] }),
      },
    })
    renderApp('/what-if')
    const card = await runDefault()
    expect(card).toHaveTextContent('Learned from 91 weeks, 2024-12-30 to 2026-09-21')
    expect(card).toHaveTextContent('Medium confidence')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('moving the slider works the scenario out again with the new number', async () => {
    const api = routes()
    renderApp('/what-if')
    await runDefault()
    const thumb = screen.getByRole('slider', { name: 'Price change' })
    thumb.focus()
    await userEvent.keyboard('{ArrowRight}{ArrowRight}')
    await waitFor(() => expect(api.count('POST /whatif/run')).toBeGreaterThan(1))
    expect((api.body('POST /whatif/run') as { change: number }).change).toBeGreaterThan(10)
  })

  it('lets the person set their own assumption, and goes back to the estimate', async () => {
    const api = routes()
    renderApp('/what-if')
    await runDefault()
    await userEvent.click(screen.getByRole('button', { name: 'Edit assumptions' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit the assumption' })
    const field = within(dialog).getByLabelText(/Change in units sold for 1% on the price/)
    await userEvent.clear(field)
    await userEvent.type(field, '-2.5')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use my figure' }))
    await waitFor(() =>
      expect((api.body('POST /whatif/run') as { assumptions?: object }).assumptions).toEqual({
        price_elasticity: -2.5,
      }),
    )
  })

  it('refuses a figure of the wrong sign before asking the server', async () => {
    const api = routes()
    renderApp('/what-if')
    await runDefault()
    await userEvent.click(screen.getByRole('button', { name: 'Edit assumptions' }))
    const dialog = await screen.findByRole('dialog')
    const field = within(dialog).getByLabelText(/Change in units sold/)
    await userEvent.clear(field)
    await userEvent.type(field, '3')
    expect(within(dialog).getByText('Enter a number between −10 and 0.')).toBeVisible()
    expect(within(dialog).getByRole('button', { name: 'Use my figure' })).toBeDisabled()
    expect(api.count('POST /whatif/run')).toBe(1)
  })

  it('marketing spend needs a channel and sends it as the scope', async () => {
    const api = routes({
      'POST /whatif/run': {
        status: 200,
        body: demoResult({
          facts: demoFacts({
            scenario: {
              metric: '',
              lever: 'marketing_spend',
              change: 20,
              scope: { channel: 'Instagram' },
              horizon: 3,
              overrides: {},
            },
            channel: 'Instagram',
            delta_expected: 256_059,
            delta_low: 206_731,
            delta_high: 306_738,
            delta_pct_expected: 16,
            units_change_pct: null,
            net_after_spend: 186_016,
            extra_spend: 70_043,
            side_effects: [{ code: 'extra_spend', amount: 70_043 }],
            assumptions: [
              {
                key: 'spend_elasticity',
                value: 0.82,
                low: 0.7,
                high: 0.95,
                source: 'history',
                editable: true,
              },
              { key: 'attribution_limits', value: true, source: 'default', editable: false },
            ],
          }),
          notes: ['what_if_assumptions', 'attribution_limits'],
        }),
      },
    })
    renderApp('/what-if')
    await userEvent.selectOptions(
      await screen.findByLabelText('What do you want to change?'),
      'marketing_spend',
    )
    expect(screen.getByRole('button', { name: 'Run scenario' })).toBeDisabled()
    await userEvent.selectOptions(await screen.findByLabelText('Channel'), 'Instagram')
    await userEvent.click(screen.getByRole('button', { name: 'Run scenario' }))
    const card = await screen.findByRole('region', { name: /^Instagram spend \+20%/ })
    expect(api.body('POST /whatif/run')).toEqual({
      lever: 'marketing_spend',
      change: 20,
      scope: { channel: 'Instagram' },
      horizon: 3,
    })
    expect(card).toHaveTextContent('Revenue by channel is as each platform reports it')
    expect(screen.getByText(/After the change in spend, the net effect is \+₹1\.9 L/)).toBeVisible()
  })

  it('explains why a scenario cannot be worked out', async () => {
    routes({
      'POST /whatif/run': apiError(
        422,
        'whatif_too_little_history',
        'There is not enough history.',
      ),
    })
    renderApp('/what-if')
    await userEvent.click(await screen.findByRole('button', { name: 'Run scenario' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'At least six months of data are needed',
    )
  })

  it('points to Connections when there is no data', async () => {
    routes({ 'GET /whatif/options': apiError(409, 'no_dictionary', 'Connect data first.') })
    renderApp('/what-if')
    expect(await screen.findByRole('link', { name: 'Connect data' })).toHaveAttribute(
      'href',
      '/connections',
    )
  })

  it('tells people with no price data what is missing', async () => {
    routes({
      'GET /whatif/options': { status: 200, body: { ...demoOptions, metrics: [], channels: null } },
    })
    renderApp('/what-if')
    expect(
      await screen.findByText(/Connect your order data and confirm the dictionary/),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Connect data' })).toHaveAttribute(
      'href',
      '/connections',
    )
  })

  it('Viewers are told their role cannot run what-ifs, and nothing is loaded', async () => {
    const api = routes()
    renderApp('/what-if', { session: viewer })
    expect(await screen.findByText(/does not include running what-ifs/)).toBeVisible()
    expect(api.count('GET /whatif/options')).toBe(0)
  })
})
