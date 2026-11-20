import type { Billing, PlanChangePreview, UsageSummary } from '../lib/billingApi'

export const demoBilling: Billing = {
  plan: 'managed',
  plans: [
    {
      key: 'managed',
      name: 'Managed AI',
      summary: 'We provide the AI. A monthly number of questions and long analyses is included.',
      monthly_questions: 500,
      needs_own_key: false,
      price_inr_per_month: null,
    },
    {
      key: 'byok',
      name: 'Your own AI key',
      summary: 'You paste your own Gemini or Claude key and pay your AI provider directly.',
      monthly_questions: null,
      needs_own_key: true,
      price_inr_per_month: null,
    },
  ],
  limit: {
    plan: 'managed',
    month: '2026-11',
    used: 410,
    limit: 500,
    percent: 82,
    level: 'warning',
    resets_on: '2026-12-01',
  },
  provider: {
    name: 'stand-in',
    live: false,
    note: 'Billing is not switched on: no payment is taken and no price is set yet.',
  },
  events: [
    {
      id: 'e1',
      kind: 'usage_warning',
      period: '2026-11',
      created_at: '2026-11-18T08:00:00Z',
      provider: 'stand-in',
      delivery: 'not_sent',
      details: { used: 400, limit: 500 },
    },
    {
      id: 'e0',
      kind: 'plan_changed',
      period: null,
      created_at: '2026-11-02T08:00:00Z',
      provider: 'stand-in',
      delivery: 'not_sent',
      details: { from: 'byok', to: 'managed' },
    },
  ],
}

export const demoSummary: UsageSummary = {
  workspace: { id: 'org-a', name: 'UrbanCart' },
  period: { month: '2026-11', start: '2026-11-01', end: '2026-11-30', timezone: 'Asia/Kolkata' },
  plan: { key: 'managed', name: 'Managed AI' },
  plan_changes: [{ at: '2026-11-02T08:00:00Z', from: 'byok', to: 'managed' }],
  lines: [
    {
      code: 'questions_included',
      description: 'Questions and long analyses included in the plan',
      quantity: 500,
      unit: 'questions',
    },
    {
      code: 'questions_used',
      description: "Questions and long analyses on the platform's AI",
      quantity: 410,
      unit: 'questions',
    },
    {
      code: 'over_limit',
      description: 'Above the included questions',
      quantity: 0,
      unit: 'questions',
    },
    {
      code: 'ai_cost_estimate',
      description: 'Estimated AI cost, for information only',
      quantity: 1.35,
      unit: 'USD',
    },
  ],
  currency: 'INR',
  amount_due_inr: 0,
  price_status: 'not_priced',
  status: 'draft_no_payment',
  notes: ['Billing is not switched on: this is a usage summary, not an invoice.'],
  frozen_at: null,
}

export const previewAllowed: PlanChangePreview = {
  current: 'managed',
  target: 'byok',
  allowed: true,
  blockers: [],
  effects: [
    { code: 'uses_own_key', provider: 'gemini', last4: '0000' },
    { code: 'no_platform_limit' },
    { code: 'provider_bills_you' },
    { code: 'effective_now' },
    { code: 'history_kept' },
    { code: 'not_charged' },
  ],
}

export const previewBlocked: PlanChangePreview = {
  current: 'managed',
  target: 'byok',
  allowed: false,
  blockers: ['key_missing'],
  effects: [{ code: 'no_platform_limit' }, { code: 'not_charged' }],
}
