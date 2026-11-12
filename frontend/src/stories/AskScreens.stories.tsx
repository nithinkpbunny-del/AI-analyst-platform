import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { ChartRenderer } from '../components/ChartRenderer'
import { API_BASE_URL } from '../lib/api'
import type { ChartSpec } from '../lib/chatApi'
import { demoSession } from '../lib/demoSession'
import {
  demoAnswer,
  demoConversation,
  demoConversations,
  demoQuestion,
  demoRunning,
  demoSaved,
} from '../testing/askFixtures'
import { dark, phone } from './variants'

// M8 screens with fixed demo data: the API is answered in the browser, nothing is sent anywhere.
const failed = { ...demoAnswer, status: 'failed' as const, answer: null, error_code: 'limit:steps' }
const conversations = {
  answer: demoConversation,
  working: { ...demoConversation, messages: [demoQuestion, demoRunning] },
  failed: { ...demoConversation, messages: [demoQuestion, failed] },
}
type State = 'new' | keyof typeof conversations

function mockFetch(state: State) {
  const original = window.fetch
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), API_BASE_URL)
    const json = (body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }),
      )
    if (url.origin === new URL(API_BASE_URL).origin) {
      if (url.pathname === '/conversations') return json({ conversations: demoConversations })
      if (url.pathname === '/saved-questions') return json({ questions: demoSaved })
      if (url.pathname === '/conversations/conv1' && state !== 'new') {
        return json(conversations[state])
      }
    }
    return original(input, init)
  }
  return () => {
    window.fetch = original
  }
}

function Screen({ state }: { state: State }) {
  return (
    <div className="-m-4">
      <Providers session={demoSession}>
        <MemoryRouter initialEntries={[state === 'new' ? '/ask' : '/ask/conv1']}>
          <AppRoutes />
        </MemoryRouter>
      </Providers>
    </div>
  )
}

const meta = {
  title: 'Screens/Ask',
  component: Screen,
  args: { state: 'answer' },
  beforeEach: ({ args }) => mockFetch(args.state),
} satisfies Meta<typeof Screen>
export default meta
type Story = StoryObj<typeof meta>

export const Answer: Story = {}
export const AnswerDark: Story = { ...dark }
export const AnswerPhone: Story = { ...phone }
export const NewQuestion: Story = { args: { state: 'new' } }
export const NewQuestionPhone: Story = { args: { state: 'new' }, ...phone }
export const Working: Story = { args: { state: 'working' } }
export const Failed: Story = { args: { state: 'failed' } }

const trend: ChartSpec = {
  chart_id: 'c2',
  type: 'line',
  title: 'Orders grew every month except during the Website gap',
  x: { name: 'bucket', values: ['2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01'] },
  series: [
    { name: 'App', data: [604, 661, 692, 710] },
    { name: 'Website', data: [360, 392, 301, 410] },
  ],
  result_id: 'r1',
  unit: 'count',
}

export const ChartBars: StoryObj<typeof ChartRenderer> = {
  render: () => (
    <div className="max-w-conversation">
      <ChartRenderer spec={demoAnswer.answer!.charts[0]!} />
    </div>
  ),
}
export const ChartLines: StoryObj<typeof ChartRenderer> = {
  render: () => (
    <div className="max-w-conversation">
      <ChartRenderer spec={trend} />
    </div>
  ),
}
export const ChartLinesDark: StoryObj<typeof ChartRenderer> = { ...ChartLines, ...dark }
