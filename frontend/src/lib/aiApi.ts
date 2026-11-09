import { authedRequest } from './api'

/** The AI plan, the workspace's own key and usage (backend `app/api/ai.py`). Keys are never sent back. */
export type AiProvider = 'gemini' | 'claude'
export type AiPlan = 'managed' | 'byok'

export interface OwnKey {
  provider: AiProvider
  last4: string
  added_at: string
  added_by_name: string | null
}

export interface AiSettings {
  plan: AiPlan
  managed: { provider: AiProvider; available: boolean; monthly_question_limit: number }
  own_key: OwnKey | null
}

export interface AiCheck {
  reply: string
  provider: AiProvider
  model: string
  key_source: AiPlan
  input_tokens: number
  output_tokens: number
}

export interface UsageTotals {
  questions: number
  limited_questions: number
  tasks: number
  briefings: number
  checks: number
  input_tokens: number
  output_tokens: number
  /** Decimal as a string, e.g. "0.024000" (USD). */
  cost_usd: string
  cost_unknown_calls: number
}

export interface PersonUsage {
  user_id: string | null
  name: string | null
  email: string | null
  questions: number
  input_tokens: number
  output_tokens: number
  cost_usd: string
}

export interface MonthUsage {
  month: string
  questions: number
  input_tokens: number
  output_tokens: number
  cost_usd: string
}

export interface Usage {
  month: string
  resets_on: string
  is_current_month: boolean
  plan: AiPlan
  /** null on BYOK: no platform limit. */
  limit: number | null
  totals: UsageTotals
  people: PersonUsage[]
  history: MonthUsage[]
}

export const aiApi = {
  settings: () => authedRequest<AiSettings>('/org/ai'),
  setPlan: (plan: AiPlan) =>
    authedRequest<AiSettings>('/org/ai/plan', { method: 'PUT', body: { plan } }),
  setKey: (provider: AiProvider, apiKey: string) =>
    authedRequest<AiSettings>('/org/ai/key', {
      method: 'PUT',
      body: { provider, api_key: apiKey },
    }),
  removeKey: () => authedRequest<void>('/org/ai/key', { method: 'DELETE' }),
  check: () => authedRequest<AiCheck>('/org/ai/check', { method: 'POST' }),
  usage: (month?: string) =>
    authedRequest<Usage>(`/org/usage${month ? `?month=${encodeURIComponent(month)}` : ''}`),
}

/** "$0.02", "$1.35"; amounts under a cent show as "< $0.01" (estimates, so no false precision). */
export function formatUsd(value: string | number): string {
  const amount = typeof value === 'string' ? Number(value) : value
  if (!Number.isFinite(amount)) return '—'
  if (amount === 0) return '$0.00'
  if (amount < 0.01) return '< $0.01'
  return `$${amount.toFixed(2)}`
}

/** "12.4K", "1.2M" for token counts. */
export function formatTokens(value: number): string {
  if (value < 1000) return String(value)
  if (value < 1_000_000) return `${(Math.round(value / 100) / 10).toString()}K`
  return `${(Math.round(value / 100_000) / 10).toString()}M`
}

/** "November 2026" from "2026-11". */
export function formatMonth(month: string): string {
  const [year = 1970, number = 1] = month.split('-').map(Number)
  return new Intl.DateTimeFormat('en-IN', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, number - 1, 1)))
}
