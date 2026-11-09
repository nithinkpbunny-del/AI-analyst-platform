import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { Providers } from '../app/Providers'
import { AiPlanSection } from '../features/settings/AiPlanSection'
import { UsageSection } from '../features/settings/UsageSection'
import type { AiSettings, Usage } from '../lib/aiApi'
import { API_BASE_URL } from '../lib/api'
import { demoSession } from '../lib/demoSession'
import { ROLE_PERMISSIONS } from '../lib/permissions'
import { demoAiSettings, demoAiSettingsByok, demoUsage } from '../testing/aiFixtures'
import { dark, phone } from './variants'

// M5 Settings sections with fixed demo data: the API is answered in the browser, nothing is sent anywhere.
function mockFetch(settings: AiSettings, usage: Usage) {
  const replies: Record<string, unknown> = {
    'GET /org/ai': settings,
    'GET /org/usage': usage,
    'POST /org/ai/check': {
      reply: 'OK',
      provider: settings.plan === 'byok' ? 'claude' : 'gemini',
      model: settings.plan === 'byok' ? 'claude-opus-5-5' : 'gemini-3.8-flash',
      key_source: settings.plan,
      input_tokens: 18,
      output_tokens: 2,
    },
  }
  const original = window.fetch
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), API_BASE_URL)
    const body = replies[`${init?.method ?? 'GET'} ${url.pathname}`]
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

function Sections({ role = 'owner' }: { role?: 'owner' | 'analyst' }) {
  const session =
    role === 'owner' ? demoSession : { ...demoSession, role, permissions: ROLE_PERMISSIONS.analyst }
  return (
    <Providers session={session}>
      <MemoryRouter>
        <div className="grid max-w-[1100px] gap-6 desktop:grid-cols-2">
          <AiPlanSection />
          <UsageSection />
        </div>
      </MemoryRouter>
    </Providers>
  )
}

const meta = {
  title: 'Screens/Settings: AI plan and usage',
  component: Sections,
  beforeEach: () => mockFetch(demoAiSettings, demoUsage),
} satisfies Meta<typeof Sections>
export default meta
type Story = StoryObj<typeof meta>

/** Managed AI, no own key: the key form is ready for an Owner who wants to switch. */
export const Managed: Story = {}
export const ManagedDark: Story = { ...dark }
export const ManagedPhone: Story = { ...phone }

/** The workspace's own Claude key is in use: no platform limit. */
export const OwnKey: Story = {
  beforeEach: () => mockFetch(demoAiSettingsByok, { ...demoUsage, plan: 'byok', limit: null }),
}

/** 90% of the month's questions used: the meter turns to the caution colour. */
export const NearTheLimit: Story = {
  beforeEach: () =>
    mockFetch(demoAiSettings, {
      ...demoUsage,
      totals: { ...demoUsage.totals, limited_questions: 451 },
    }),
}

/** Analysts and Viewers only see which plan is in use. */
export const ReadOnly: Story = { args: { role: 'analyst' } }
