import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { API_BASE_URL } from '../lib/api'
import { demoSession } from '../lib/demoSession'
import {
  demoBriefing,
  demoBriefingSettings,
  demoOrg,
  demoRuns,
  demoShares,
  demoTaskReport,
  demoTasks,
  demoToday,
} from '../testing/reportsFixtures'
import { dark, phone } from './variants'

// M10: Today, Briefings, Tasks and a report, with fixed demo data; the API is answered in the browser.
const ROUTES: Record<string, unknown> = {
  '/today': demoToday,
  '/org': demoOrg(),
  '/tasks': demoTasks,
  '/briefings/settings': demoBriefingSettings,
  '/briefings': { runs: demoRuns },
  '/reports/rep-briefing': demoBriefing,
  '/reports/rep-task': demoTaskReport,
  '/shares': demoShares,
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
  title: 'Screens/Today, briefings and tasks',
  component: Screen,
  args: { path: '/today' },
  beforeEach: () => mockFetch(),
} satisfies Meta<typeof Screen>
export default meta
type Story = StoryObj<typeof meta>

/** The home screen: onboarding checklist, the morning briefing with its key numbers, questions to ask. */
export const Today: Story = {}
export const TodayDark: Story = { ...dark }
export const TodayPhone: Story = { ...phone }
export const Briefings: Story = { args: { path: '/briefings' } }
export const BriefingsPhone: Story = { args: { path: '/briefings' }, ...phone }
export const Tasks: Story = { args: { path: '/tasks' } }
export const TasksDark: Story = { args: { path: '/tasks' }, ...dark }
/** A briefing as a report: press Export or Share for the dialogs. */
export const BriefingReport: Story = { args: { path: '/reports/rep-briefing' } }
export const TaskReportPhone: Story = { args: { path: '/reports/rep-task' }, ...phone }
