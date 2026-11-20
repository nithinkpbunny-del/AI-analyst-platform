import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { API_BASE_URL } from '../lib/api'
import { demoSession } from '../lib/demoSession'
import { demoDashboard, demoGoalList, demoGoalOptions } from '../testing/goalsFixtures'
import { dark, phone } from './variants'

// M15: Goals and the Dashboard, with fixed demo data; the API is answered in the browser.
const ROUTES: Record<string, unknown> = {
  '/goals': demoGoalList,
  '/goals/refresh': demoGoalList,
  '/goals/options': demoGoalOptions,
  '/dashboard': demoDashboard,
  '/dashboard/refresh': demoDashboard,
}

function mockFetch() {
  const original = window.fetch
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), API_BASE_URL)
    if (url.origin === new URL(API_BASE_URL).origin && url.pathname in ROUTES) {
      return Promise.resolve(
        new Response(JSON.stringify(ROUTES[url.pathname]), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    return original(input, init)
  }
  return () => {
    window.fetch = original
  }
}

function Screen({ path }: { path: string }) {
  return (
    <div className="-m-4">
      <Providers session={demoSession}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </Providers>
    </div>
  )
}

const meta = {
  title: 'Screens/Goals and dashboard',
  component: Screen,
  args: { path: '/goals' },
  beforeEach: () => mockFetch(),
} satisfies Meta<typeof Screen>
export default meta
type Story = StoryObj<typeof meta>

/** Goals: progress against the target, the projection with its time marker, the early warning. */
export const Goals: Story = {}
export const GoalsDark: Story = { ...dark }
export const GoalsPhone: Story = { ...phone }
/** The dashboard: your tiles (open a tile's menu for Move, Settings, Remove) and the team's. */
export const Dashboard: Story = { args: { path: '/dashboard' } }
export const DashboardDark: Story = { args: { path: '/dashboard' }, ...dark }
export const DashboardPhone: Story = { args: { path: '/dashboard' }, ...phone }
