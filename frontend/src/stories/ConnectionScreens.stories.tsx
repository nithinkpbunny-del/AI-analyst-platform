import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { API_BASE_URL } from '../lib/api'
import { demoSession } from '../lib/demoSession'
import { ROLE_PERMISSIONS } from '../lib/permissions'
import {
  demoAccess,
  demoConnectionList,
  demoOrdersDetail,
  demoDatasetPreview,
} from '../testing/connectionFixtures'
import { dark, phone } from './variants'

// M6 screens with fixed demo data: the API is answered in the browser, nothing is sent anywhere.
const replies: Record<string, unknown> = {
  '/connections': demoConnectionList,
  '/connections/access': { people: demoAccess },
  '/connections/c1': demoOrdersDetail,
  '/connections/c1/datasets/d1/preview': demoDatasetPreview,
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

function Screen({ path, analyst = false }: { path: string; analyst?: boolean }) {
  const session = analyst
    ? { ...demoSession, role: 'analyst' as const, permissions: ROLE_PERMISSIONS.analyst }
    : demoSession
  return (
    <div className="-m-4">
      <Providers session={session}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </Providers>
    </div>
  )
}

const meta = {
  title: 'Screens/Connections',
  component: Screen,
  args: { path: '/connections' },
  beforeEach: mockFetch,
} satisfies Meta<typeof Screen>
export default meta
type Story = StoryObj<typeof meta>

export const Connections: Story = {}
export const ConnectionsDark: Story = { ...dark }
export const ConnectionsPhone: Story = { ...phone }
export const ConnectionsForAnalysts: Story = { args: { analyst: true } }
export const Connection: Story = { args: { path: '/connections/c1' } }
export const ConnectionDark: Story = { args: { path: '/connections/c1' }, ...dark }
export const ConnectionPhone: Story = { args: { path: '/connections/c1' }, ...phone }
