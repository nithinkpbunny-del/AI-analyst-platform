import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Conversation } from '../../lib/chatApi'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import {
  demoAnswer,
  demoConversation,
  demoConversations,
  demoQuestion,
  demoRunning,
  demoSaved,
} from '../../testing/askFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'

type Routes = Parameters<typeof fakeApi>[0]

function routes(extra: Routes = {}) {
  return fakeApi({
    'GET /conversations': { status: 200, body: { conversations: demoConversations } },
    'GET /saved-questions': { status: 200, body: { questions: demoSaved } },
    'GET /conversations/conv1': { status: 200, body: demoConversation },
    ...extra,
  })
}

const viewer = { ...demoSession, role: 'viewer' as const, permissions: ROLE_PERMISSIONS.viewer }

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('Asking', () => {
  it('starts with a lens, starters and saved questions, and asks with Enter', async () => {
    const api = routes({
      'POST /conversations': { status: 201, body: demoConversation },
    })
    renderApp('/ask')
    expect(await screen.findByRole('heading', { name: 'Ask', level: 1 })).toBeVisible()
    await userEvent.click(screen.getByRole('radio', { name: 'Marketing' }))
    const box = screen.getByLabelText('Your question')
    await userEvent.type(box, 'Which channel{shift>}{enter}{/shift}works best?')
    expect(box).toHaveValue('Which channel\nworks best?')
    await userEvent.type(box, '{enter}')
    await waitFor(() => expect(api.count('POST /conversations')).toBe(1))
    expect(api.body('POST /conversations')).toEqual({
      question: 'Which channel\nworks best?',
      lens: 'marketing',
    })
    expect(await screen.findByRole('heading', { name: demoConversation.title })).toBeVisible()
  })

  it('a starter question is asked with one tap', async () => {
    const api = routes({ 'POST /conversations': { status: 201, body: demoConversation } })
    renderApp('/ask')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Which marketing channel gives the best return?' }),
    )
    await waitFor(() =>
      expect(api.body('POST /conversations')).toEqual({
        question: 'Which marketing channel gives the best return?',
        lens: 'business',
      }),
    )
  })

  it('a saved question runs again and can be removed', async () => {
    const api = routes({
      'POST /conversations': { status: 201, body: demoConversation },
      'DELETE /saved-questions/s1': { status: 204 },
    })
    renderApp('/ask')
    const history = await screen.findByRole('complementary', { name: 'Your questions' })
    await userEvent.click(
      await within(history).findByRole('button', {
        name: /^How did revenue change last month\?/,
      }),
    )
    await waitFor(() => expect(api.count('POST /conversations')).toBe(1))
    await userEvent.click(
      within(history).getByRole('button', { name: 'Remove How did revenue change last month?' }),
    )
    expect(await screen.findByText('Saved question removed.')).toBeVisible()
  })

  it('`/` focuses the question box', async () => {
    routes()
    renderApp('/ask')
    await screen.findByLabelText('Your question')
    fireEvent.keyDown(window, { key: '/' })
    expect(screen.getByLabelText('Your question')).toHaveFocus()
  })

  it('shows why a question cannot be asked', async () => {
    routes({
      'POST /conversations': apiError(
        429,
        'usage_limit_reached',
        'This workspace has used all 500 questions for this month.',
      ),
    })
    renderApp('/ask')
    await userEvent.type(await screen.findByLabelText('Your question'), 'How are sales?{enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('used all 500 questions')
  })

  it('Viewers are told their role cannot ask', async () => {
    const api = routes()
    renderApp('/ask', { session: viewer })
    expect(await screen.findByText(/Your role does not include asking questions/)).toBeVisible()
    expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument()
    expect(api.count('GET /conversations')).toBe(0)
  })
})

describe('An answer', () => {
  it('shows the lead with its key figure, evidence with sources, notes and next questions', async () => {
    routes()
    renderApp('/ask/conv1')
    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(within(answer).getByText('17.7%', { selector: 'mark, span, *' })).toBeVisible()
    // Citations become numbered source marks, not "[r1]".
    expect(answer).not.toHaveTextContent('[r1]')
    const mark = within(answer).getAllByRole('button', { name: /^Source 1: Shopify orders/ })[0]
    await userEvent.click(mark!)
    expect(await screen.findByText('Shopify orders · shopify_orders')).toBeVisible()
    expect(screen.getByText('Driver analysis, 4 rows')).toBeVisible()
    expect(within(answer).getByText(/Repeat customers explain 88.9%/)).toBeVisible()
    expect(within(answer).getByText(/The business dictionary is not confirmed yet/)).toBeVisible()
    expect(within(answer).getByText(/list prices/)).toBeVisible()
    expect(screen.getByRole('note')).toHaveTextContent('shopify_orders:')
    expect(screen.getByRole('figure')).toHaveTextContent(
      'South fell 17.7% while every other region grew',
    )
  })

  it('a next question is asked as a follow-up', async () => {
    const api = routes({
      'POST /conversations/conv1/messages': {
        status: 201,
        body: {
          ...demoConversation,
          messages: [...demoConversation.messages, { ...demoQuestion, id: 'm3' }, demoRunning],
        },
      },
    })
    renderApp('/ask/conv1')
    await userEvent.click(
      await screen.findByRole('button', {
        name: 'Which Electronics products lost the most South customers?',
      }),
    )
    await waitFor(() =>
      expect(api.body('POST /conversations/conv1/messages')).toEqual({
        question: 'Which Electronics products lost the most South customers?',
      }),
    )
  })

  it('shows progress while it is made, then the answer', async () => {
    let polls = 0
    const working: Conversation = { ...demoConversation, messages: [demoQuestion, demoRunning] }
    routes({
      'GET /conversations/conv1': () => {
        polls += 1
        return { status: 200, body: polls < 2 ? working : demoConversation }
      },
    })
    renderApp('/ask/conv1')
    const progress = await screen.findByRole('list', { name: 'Progress' })
    expect(within(progress).getByText('Fetching shopify_orders from Shopify orders')).toBeVisible()
    expect(within(progress).getByText('Reading your question')).toBeVisible()
    expect(screen.getByLabelText('Your question')).toBeDisabled()
    expect(await screen.findByRole('article', { name: 'Answer' }, { timeout: 3000 })).toBeVisible()
  })

  it('a failed answer says why and offers to ask again', async () => {
    const failed = {
      ...demoAnswer,
      status: 'failed' as const,
      answer: null,
      error_code: 'limit:steps',
    }
    const api = routes({
      'GET /conversations/conv1': {
        status: 200,
        body: { ...demoConversation, messages: [demoQuestion, failed] },
      },
      'POST /conversations/conv1/messages': { status: 201, body: demoConversation },
    })
    renderApp('/ask/conv1')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This question needed more steps than one question allows',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Ask again' }))
    await waitFor(() =>
      expect(api.body('POST /conversations/conv1/messages')).toEqual({
        question: demoQuestion.content,
      }),
    )
  })

  it('an AI set-up problem points to Settings', async () => {
    const failed = {
      ...demoAnswer,
      status: 'failed' as const,
      answer: null,
      error_code: 'llm:quota',
    }
    routes({
      'GET /conversations/conv1': {
        status: 200,
        body: { ...demoConversation, messages: [demoQuestion, failed] },
      },
    })
    renderApp('/ask/conv1')
    expect(await screen.findByRole('alert')).toHaveTextContent('used up its quota')
  })

  it('"Show my work" lists the steps, queries and sources', async () => {
    routes()
    renderApp('/ask/conv1')
    await userEvent.click(await screen.findByRole('button', { name: 'Show my work' }))
    const drawer = await screen.findByRole('dialog', { name: 'How this answer was made' })
    expect(within(drawer).getByText('1. Driver analysis')).toBeVisible()
    expect(within(drawer).getByLabelText('Query')).toHaveTextContent('SELECT CAST(shipping_region')
    expect(within(drawer).getByText(/This step failed \(sql_error\)/)).toBeVisible()
    expect(within(drawer).getByText('Shopify products')).toBeVisible()
  })

  it('saves the question, changes the lens and deletes the conversation', async () => {
    const api = routes({
      'POST /saved-questions': {
        status: 201,
        body: { id: 's2', text: demoQuestion.content, lens: 'business', created_at: '' },
      },
      'PATCH /conversations/conv1': {
        status: 200,
        body: { ...demoConversations[0], lens: 'finance' },
      },
      'DELETE /conversations/conv1': { status: 204 },
    })
    renderApp('/ask/conv1')
    await userEvent.click(await screen.findByRole('button', { name: 'Save question' }))
    expect(await screen.findByText('Question saved.')).toBeVisible()
    expect(api.body('POST /saved-questions')).toEqual({
      text: demoQuestion.content,
      lens: 'business',
    })
    await userEvent.click(screen.getByRole('radio', { name: 'Finance' }))
    await waitFor(() => expect(api.body('PATCH /conversations/conv1')).toEqual({ lens: 'finance' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete this conversation?' })
    await userEvent.click(within(confirm).getByRole('button', { name: 'Delete conversation' }))
    expect(await screen.findByText('Conversation deleted.')).toBeVisible()
    expect(api.count('DELETE /conversations/conv1')).toBe(1)
  })

  it('says when a conversation does not exist', async () => {
    routes({ 'GET /conversations/gone': apiError(404, 'not_found', 'Not found.') })
    renderApp('/ask/gone')
    expect(
      await screen.findByText('This conversation could not be found. It may have been deleted.'),
    ).toBeVisible()
  })
})
