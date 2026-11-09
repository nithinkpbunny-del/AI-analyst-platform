import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { formatMonth, formatTokens, formatUsd } from '../../lib/aiApi'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import { demoAiSettings, demoAiSettingsByok, demoUsage } from '../../testing/aiFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'

beforeEach(() => {
  window.localStorage.clear()
})

type Reply = { status: number; body?: unknown }

function aiRoutes(extra: Record<string, Reply | ((init: RequestInit) => Reply)> = {}) {
  return fakeApi({
    'GET /org/ai': { status: 200, body: demoAiSettings },
    'GET /org/usage': { status: 200, body: demoUsage },
    ...extra,
  })
}

async function aiSection() {
  return within(
    (await screen.findByRole('heading', { name: 'AI plan' })).closest('section') as HTMLElement,
  )
}

describe('AI plan (Owner)', () => {
  it('shows Managed AI with its limit and the privacy notice', async () => {
    aiRoutes()
    renderApp('/settings')
    const section = await aiSection()
    const managed = await section.findByRole('radio', { name: /Managed AI/ })
    expect(managed).toBeChecked()
    expect(section.getByText(/Google Gemini\. Includes 500 questions a month/)).toBeInTheDocument()
    expect(section.getByText(/does not train on your data/)).toBeInTheDocument()
    expect(section.getByText('No key added yet.')).toBeInTheDocument()
  })

  it('choosing "Your own key" without a key asks for one, then saves and switches', async () => {
    const api = aiRoutes({
      'PUT /org/ai/key': {
        status: 200,
        body: { ...demoAiSettingsByok, plan: 'managed' },
      },
      'PUT /org/ai/plan': { status: 200, body: demoAiSettingsByok },
    })
    renderApp('/settings')
    const section = await aiSection()
    await userEvent.click(await section.findByRole('radio', { name: /Your own key/ }))
    expect(api.count('PUT /org/ai/plan')).toBe(0) // nothing to switch to yet
    await userEvent.selectOptions(section.getByRole('combobox', { name: 'Provider' }), 'claude')
    await userEvent.type(section.getByLabelText('API key'), '  sk-ant-api03-test-key-000000000000 ')
    await userEvent.click(section.getByRole('button', { name: 'Save key and switch' }))
    expect(await section.findByText('This workspace now uses its own key.')).toBeInTheDocument()
    expect(api.body('PUT /org/ai/key')).toEqual({
      provider: 'claude',
      api_key: 'sk-ant-api03-test-key-000000000000',
    })
    expect(api.body('PUT /org/ai/plan')).toEqual({ plan: 'byok' })
    expect(section.getByText('Anthropic Claude key ending in ••••x7Qa')).toBeInTheDocument()
    expect(section.queryByLabelText('API key')).not.toBeInTheDocument() // the form closes once saved
  })

  it('shows why a key was not accepted, next to the field', async () => {
    aiRoutes({
      'PUT /org/ai/key': apiError(422, 'ai_invalid_key', 'Google Gemini did not accept this key.', {
        api_key: 'Google Gemini did not accept this key. Check that you copied all of it.',
      }),
    })
    renderApp('/settings')
    const section = await aiSection()
    await userEvent.type(
      await section.findByLabelText('API key'),
      'AIzaSyWrongKey000000000000000000',
    )
    await userEvent.click(section.getByRole('button', { name: 'Save key' }))
    const field = section.getByLabelText('API key')
    await waitFor(() => expect(field).toHaveAttribute('aria-invalid', 'true'))
    expect(
      section.getByText('Google Gemini did not accept this key. Check that you copied all of it.'),
    ).toBeInTheDocument()
  })

  it('an empty key is caught before calling the API', async () => {
    const api = aiRoutes()
    renderApp('/settings')
    const section = await aiSection()
    await userEvent.click(await section.findByRole('button', { name: 'Save key' }))
    expect(section.getByText('This field is required.')).toBeInTheDocument()
    expect(api.count('PUT /org/ai/key')).toBe(0)
  })

  it('shows only the last four characters of a saved key, and removes it after confirming', async () => {
    let settings = demoAiSettingsByok
    const api = aiRoutes({
      'GET /org/ai': () => ({ status: 200, body: settings }),
      'DELETE /org/ai/key': () => {
        settings = demoAiSettings
        return { status: 204 }
      },
    })
    renderApp('/settings')
    const section = await aiSection()
    expect(await section.findByText('Anthropic Claude key ending in ••••x7Qa')).toBeInTheDocument()
    expect(section.getByText('Added by Asha Rao on 30 Oct 2026')).toBeInTheDocument()
    expect(section.getByRole('radio', { name: /Your own key/ })).toBeChecked()
    expect(section.getByText(/provider’s terms for that key apply/)).toBeInTheDocument()
    await userEvent.click(section.getByRole('button', { name: 'Remove key' }))
    const dialog = await screen.findByRole('dialog', { name: 'Remove your own key?' })
    expect(dialog).toHaveTextContent('The Anthropic Claude key ending in x7Qa is deleted')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove key' }))
    expect(
      await section.findByText('Key removed. The workspace uses Managed AI again.'),
    ).toBeInTheDocument()
    expect(api.count('DELETE /org/ai/key')).toBe(1)
    expect(section.getByRole('radio', { name: /Managed AI/ })).toBeChecked()
  })

  it('switches back to Managed AI', async () => {
    const api = aiRoutes({
      'GET /org/ai': { status: 200, body: demoAiSettingsByok },
      'PUT /org/ai/plan': { status: 200, body: { ...demoAiSettingsByok, plan: 'managed' } },
    })
    renderApp('/settings')
    const section = await aiSection()
    await userEvent.click(await section.findByRole('radio', { name: /Managed AI/ }))
    expect(await section.findByText('This workspace now uses Managed AI.')).toBeInTheDocument()
    expect(api.body('PUT /org/ai/plan')).toEqual({ plan: 'managed' })
  })

  it('tests the AI connection and shows the answer', async () => {
    aiRoutes({
      'POST /org/ai/check': {
        status: 200,
        body: {
          reply: 'OK',
          provider: 'gemini',
          model: 'gemini-3.8-flash',
          key_source: 'managed',
          input_tokens: 18,
          output_tokens: 2,
        },
      },
    })
    renderApp('/settings')
    const section = await aiSection()
    await userEvent.click(await section.findByRole('button', { name: 'Test AI connection' }))
    expect(await section.findByRole('status')).toHaveTextContent(
      'Google Gemini answered “OK” using gemini-3.8-flash (20 tokens).',
    )
  })

  it('explains provider problems in plain words', async () => {
    aiRoutes({
      'POST /org/ai/check': apiError(502, 'ai_quota', 'server text'),
    })
    renderApp('/settings')
    const section = await aiSection()
    await userEvent.click(await section.findByRole('button', { name: 'Test AI connection' }))
    expect(await section.findByRole('alert')).toHaveTextContent(
      'The key has used up its quota with the provider.',
    )
  })

  it('warns when Managed AI is not set up on the server', async () => {
    aiRoutes({
      'GET /org/ai': {
        status: 200,
        body: { ...demoAiSettings, managed: { ...demoAiSettings.managed, available: false } },
      },
    })
    renderApp('/settings')
    const section = await aiSection()
    expect(await section.findByText(/Managed AI is not set up on this server/)).toBeInTheDocument()
  })
})

describe('AI plan (Analyst and Viewer)', () => {
  it('shows the plan read-only and no usage', async () => {
    const api = aiRoutes()
    renderApp('/settings', {
      session: {
        ...demoSession,
        role: 'analyst',
        permissions: ROLE_PERMISSIONS.analyst,
        organization: { ...demoSession.organization, plan: 'byok' },
      },
    })
    const section = await aiSection()
    expect(
      section.getByText('This workspace uses Your own key. Only Owners can change it.'),
    ).toBeInTheDocument()
    expect(section.queryByRole('radio')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'AI usage' })).not.toBeInTheDocument()
    expect(api.count('GET /org/ai')).toBe(0)
    expect(api.count('GET /org/usage')).toBe(0)
  })
})

describe('AI usage (Owner)', () => {
  async function usageSection() {
    return within(
      (await screen.findByRole('heading', { name: 'AI usage' })).closest('section') as HTMLElement,
    )
  }

  it('shows the monthly limit as a meter, totals, people and six months', async () => {
    aiRoutes()
    renderApp('/settings')
    const section = await usageSection()
    const meter = await section.findByRole('meter', { name: 'Questions used this month' })
    expect(meter).toHaveAttribute('aria-valuenow', '142')
    expect(meter).toHaveAttribute('aria-valuemax', '500')
    expect(section.getByText('142 of 500 questions used')).toBeInTheDocument()
    expect(section.getByText('358 left until 1 Dec 2026')).toBeInTheDocument()
    expect(section.getByText('2.8M in · 312.5K out')).toBeInTheDocument()
    expect(section.getByText('$1.63')).toBeInTheDocument()
    const people = section.getByRole('table')
    expect(within(people).getByRole('rowheader', { name: /Ravi Kumar/ })).toBeInTheDocument()
    expect(within(people).getAllByRole('row')).toHaveLength(3)
    expect(within(section.getByRole('list')).getByText('October 2026')).toBeInTheDocument()
    expect(section.getByText('211 questions · $2.37')).toBeInTheDocument()
  })

  it('says when the limit is reached', async () => {
    aiRoutes({
      'GET /org/usage': {
        status: 200,
        body: { ...demoUsage, totals: { ...demoUsage.totals, limited_questions: 500 } },
      },
    })
    renderApp('/settings')
    const section = await usageSection()
    expect(
      await section.findByText('All questions for this month are used. They reset on 1 Dec 2026.'),
    ).toBeInTheDocument()
  })

  it('says how many calls have no price, in singular and plural', async () => {
    aiRoutes({
      'GET /org/usage': {
        status: 200,
        body: { ...demoUsage, totals: { ...demoUsage.totals, cost_unknown_calls: 1 } },
      },
    })
    renderApp('/settings')
    const section = await usageSection()
    expect(
      await section.findByText(/Not known for 1 call: its model is not on the price list\./),
    ).toBeInTheDocument()
  })

  it('has no limit on the workspace’s own key', async () => {
    aiRoutes({
      'GET /org/usage': { status: 200, body: { ...demoUsage, plan: 'byok', limit: null } },
    })
    renderApp('/settings')
    const section = await usageSection()
    expect(
      await section.findByText('No monthly limit: this workspace uses its own key.'),
    ).toBeInTheDocument()
    expect(section.queryByRole('meter')).not.toBeInTheDocument()
  })

  it('opens another month', async () => {
    const api = aiRoutes({
      'GET /org/usage': () => ({ status: 200, body: demoUsage }),
    })
    renderApp('/settings')
    const section = await usageSection()
    await userEvent.selectOptions(
      await section.findByRole('combobox', { name: 'Month' }),
      '2026-09',
    )
    await waitFor(() =>
      expect(api.calls.some((call) => call.url.endsWith('/org/usage?month=2026-09'))).toBe(true),
    )
  })
})

describe('formatting', () => {
  it('formats estimated dollars, tokens and months', () => {
    expect(formatUsd('0')).toBe('$0.00')
    expect(formatUsd('0.004')).toBe('< $0.01')
    expect(formatUsd('1.635')).toBe('$1.64')
    expect(formatTokens(950)).toBe('950')
    expect(formatTokens(12_400)).toBe('12.4K')
    expect(formatTokens(2_840_000)).toBe('2.8M')
    expect(formatMonth('2026-11')).toBe('November 2026')
  })
})

describe('AI plan: changing your mind', () => {
  it('going back to Managed AI before saving a key sends nothing', async () => {
    const api = aiRoutes()
    renderApp('/settings')
    const section = await aiSection()
    await userEvent.click(await section.findByRole('radio', { name: /Your own key/ }))
    expect(section.getByRole('button', { name: 'Save key and switch' })).toBeInTheDocument()
    await userEvent.click(section.getByRole('radio', { name: /Managed AI/ }))
    expect(section.getByRole('button', { name: 'Save key' })).toBeInTheDocument()
    expect(api.count('PUT /org/ai/plan')).toBe(0)
  })
})
