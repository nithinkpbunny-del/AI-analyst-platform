import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { WorkspaceSwitcher } from '../components/WorkspaceSwitcher'
import { API_BASE_URL } from '../lib/api'
import type { SessionInfo } from '../lib/authApi'
import { demoSession } from '../lib/demoSession'
import { demoAudit, demoInvitations, demoMembers } from '../testing/teamFixtures'
import { dark, phone } from './variants'

// M4 screens with fixed demo data: the API is answered in the browser, nothing is sent anywhere.
const replies: Record<string, unknown> = {
  '/team/members': { members: demoMembers },
  '/team/invitations': { invitations: demoInvitations },
  '/audit': { entries: demoAudit, next_cursor: null, scope: 'workspace' },
}

function mockFetch() {
  const original = window.fetch
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), API_BASE_URL)
    const body = replies[url.pathname]
    if (url.origin === new URL(API_BASE_URL).origin && body !== undefined) {
      return Promise.resolve(
        new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }),
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
  title: 'Screens/Team and activity',
  component: Screen,
  args: { path: '/team' },
  beforeEach: mockFetch,
} satisfies Meta<typeof Screen>
export default meta
type Story = StoryObj<typeof meta>

export const Team: Story = {}
export const TeamDark: Story = { ...dark }
export const TeamPhone: Story = { ...phone }
export const Activity: Story = { args: { path: '/activity' } }
export const ActivityDark: Story = { args: { path: '/activity' }, ...dark }
export const ActivityPhone: Story = { args: { path: '/activity' }, ...phone }

const twoWorkspaces: SessionInfo = {
  ...demoSession,
  memberships: [
    ...demoSession.memberships,
    { organization_id: 'o2', organization_name: 'Chaiwala Foods', role: 'viewer' },
  ],
}

/** The workspace switcher on its own, for someone in two workspaces. */
export const Switcher: StoryObj = {
  render: () => (
    <Providers session={twoWorkspaces}>
      <MemoryRouter>
        <div className="w-60 rounded-panel border border-line bg-surface p-3">
          <WorkspaceSwitcher />
        </div>
      </MemoryRouter>
    </Providers>
  ),
}
export const SwitcherDark: StoryObj = { ...Switcher, ...dark }
