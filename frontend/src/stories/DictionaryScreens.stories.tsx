import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { API_BASE_URL } from '../lib/api'
import { demoSession } from '../lib/demoSession'
import { ROLE_PERMISSIONS } from '../lib/permissions'
import {
  confirmedState,
  confirmedWithDraftState,
  firstDraftState,
} from '../testing/dictionaryFixtures'
import { dark, phone } from './variants'

// M7 screens with fixed demo data: the API is answered in the browser, nothing is sent anywhere.
const states = {
  first: firstDraftState,
  confirmed: confirmedState,
  changed: confirmedWithDraftState,
}

function mockFetch(state: keyof typeof states) {
  const reply = states[state]
  const original = window.fetch
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), API_BASE_URL)
    if (url.origin === new URL(API_BASE_URL).origin && url.pathname === '/dictionary') {
      return Promise.resolve(
        new Response(JSON.stringify(reply), { headers: { 'Content-Type': 'application/json' } }),
      )
    }
    return original(input, init)
  }
  return () => {
    window.fetch = original
  }
}

function Screen({
  analyst = false,
}: {
  state: 'first' | 'confirmed' | 'changed'
  analyst?: boolean
}) {
  const session = analyst
    ? { ...demoSession, role: 'analyst' as const, permissions: ROLE_PERMISSIONS.analyst }
    : demoSession
  return (
    <div className="-m-4">
      <Providers session={session}>
        <MemoryRouter initialEntries={['/dictionary']}>
          <AppRoutes />
        </MemoryRouter>
      </Providers>
    </div>
  )
}

const meta = {
  title: 'Screens/Dictionary',
  component: Screen,
  args: { state: 'first' },
  beforeEach: ({ args }) => mockFetch(args.state),
} satisfies Meta<typeof Screen>
export default meta
type Story = StoryObj<typeof meta>

export const FirstDraftToReview: Story = {}
export const FirstDraftDark: Story = { ...dark }
export const FirstDraftPhone: Story = { ...phone }
export const Confirmed: Story = { args: { state: 'confirmed' } }
export const ChangedDataDraft: Story = { args: { state: 'changed' } }
export const ForAnalysts: Story = { args: { state: 'confirmed', analyst: true } }
