import type { AiSettings, Usage } from '../lib/aiApi'

/** UrbanCart on Managed AI, no own key (as GET /org/ai returns it). Shared by tests, stories and screenshots. */
export const demoAiSettings: AiSettings = {
  plan: 'managed',
  managed: { provider: 'gemini', available: true, monthly_question_limit: 500 },
  own_key: null,
}

export const demoAiSettingsByok: AiSettings = {
  plan: 'byok',
  managed: { provider: 'gemini', available: true, monthly_question_limit: 500 },
  own_key: {
    provider: 'claude',
    last4: 'x7Qa',
    added_at: '2026-10-30T04:30:00+00:00',
    added_by_name: 'Asha Rao',
  },
}

export const demoUsage: Usage = {
  month: '2026-11',
  resets_on: '2026-12-01',
  is_current_month: true,
  plan: 'managed',
  limit: 500,
  totals: {
    questions: 142,
    limited_questions: 142,
    tasks: 6,
    briefings: 27,
    checks: 2,
    input_tokens: 2_840_000,
    output_tokens: 312_500,
    cost_usd: '1.633250',
    cost_unknown_calls: 0,
  },
  people: [
    {
      user_id: 'u2',
      name: 'Ravi Kumar',
      email: 'ravi@urbancart.in',
      questions: 97,
      input_tokens: 1_900_000,
      output_tokens: 210_000,
      cost_usd: '1.095000',
    },
    {
      user_id: 'u1',
      name: 'Asha Rao',
      email: 'asha@urbancart.in',
      questions: 45,
      input_tokens: 940_000,
      output_tokens: 102_500,
      cost_usd: '0.538250',
    },
  ],
  history: [
    { month: '2026-06', questions: 0, input_tokens: 0, output_tokens: 0, cost_usd: '0' },
    { month: '2026-07', questions: 0, input_tokens: 0, output_tokens: 0, cost_usd: '0' },
    { month: '2026-08', questions: 0, input_tokens: 0, output_tokens: 0, cost_usd: '0' },
    {
      month: '2026-09',
      questions: 38,
      input_tokens: 700_000,
      output_tokens: 80_000,
      cost_usd: '0.410000',
    },
    {
      month: '2026-10',
      questions: 211,
      input_tokens: 4_100_000,
      output_tokens: 455_000,
      cost_usd: '2.367500',
    },
    {
      month: '2026-11',
      questions: 142,
      input_tokens: 2_840_000,
      output_tokens: 312_500,
      cost_usd: '1.633250',
    },
  ],
}
