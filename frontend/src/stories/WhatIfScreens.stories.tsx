import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { API_BASE_URL } from '../lib/api'
import { demoSession } from '../lib/demoSession'
import { demoFacts, demoOptions, demoResult } from '../testing/whatIfFixtures'
import { dark, phone } from './variants'

// M9: the What-if screen with fixed demo data; the API is answered in the browser, nothing is sent anywhere.
type State = 'empty' | 'result' | 'history'

const fromHistory = demoResult({
  facts: demoFacts({
    scenario: {
      metric: 'revenue',
      lever: 'price',
      change: 10,
      scope: { product_type: 'Electronics', shipping_region: 'South' },
      horizon: 3,
      overrides: {},
    },
    delta_expected: 79_999,
    delta_low: 27_224,
    delta_high: 134_240,
    delta_pct_expected: 4.3,
    delta_pct_low: 1.5,
    delta_pct_high: 7.2,
    scenario_total: 1_951_843,
    scenario_low: 1_700_000,
    scenario_high: 2_150_000,
    baseline_total: 1_871_844,
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
      {
        key: 'baseline_method',
        value: 'trend_x_seasonal_index',
        source: 'history',
        editable: false,
      },
      { key: 'nothing_else_changes', value: true, source: 'default', editable: false },
    ],
    confidence: { level: 'medium', reason: 'some_history' },
    informed_by: { source: 'history', weeks: 91, start: '2024-12-30', end: '2026-09-21' },
  }),
  notes: ['what_if_assumptions'],
})

function mockFetch(state: State) {
  const original = window.fetch
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), API_BASE_URL)
    const json = (body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }),
      )
    if (url.origin === new URL(API_BASE_URL).origin) {
      if (url.pathname === '/whatif/options') return json(demoOptions)
      if (url.pathname === '/whatif/values') {
        return json({ values: ['Electronics', 'Fashion', 'Home & Kitchen'] })
      }
      if (url.pathname === '/whatif/run')
        return json(state === 'history' ? fromHistory : demoResult())
    }
    return original(input, init)
  }
  return () => {
    window.fetch = original
  }
}

// The state only picks what the mocked API answers (see ``mockFetch``); the screen itself is the same.
function Screen({ state }: { state: State }) {
  return (
    <div className="-m-4" data-state={state}>
      <Providers session={demoSession}>
        <MemoryRouter initialEntries={['/what-if']}>
          <AppRoutes />
        </MemoryRouter>
      </Providers>
    </div>
  )
}

const meta = {
  title: 'Screens/WhatIf',
  component: Screen,
  args: { state: 'empty' },
  beforeEach: ({ args }) => mockFetch(args.state),
} satisfies Meta<typeof Screen>
export default meta
type Story = StoryObj<typeof meta>

/** The form before anything is run. Press "Run scenario" to see a result with a typical figure (low confidence). */
export const Form: Story = {}
export const FormDark: Story = { ...dark }
export const FormPhone: Story = { ...phone }
/** Press "Run scenario": the same screen with an estimate learned from history (medium confidence). */
export const LearnedFromHistory: Story = { args: { state: 'history' } }
export const LearnedFromHistoryPhone: Story = { args: { state: 'history' }, ...phone }
