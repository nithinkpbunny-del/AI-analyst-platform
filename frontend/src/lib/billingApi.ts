import { authedDownload, authedRequest } from './api'
import type { AiPlan } from './aiApi'

/** Billing hooks (backend `app/api/billing.py`). There is no payment, no price and no gateway: see D117-D121. */
export type LimitLevel = 'none' | 'ok' | 'warning' | 'reached'

export interface BillingPlan {
  key: AiPlan
  name: string
  summary: string
  /** null: no platform limit. */
  monthly_questions: number | null
  needs_own_key: boolean
  /** null: not priced yet ("coming soon"). */
  price_inr_per_month: number | null
}

export interface Limit {
  plan: AiPlan
  month: string
  used: number
  limit: number | null
  percent: number | null
  level: LimitLevel
  resets_on: string
}

export interface BillingEvent {
  id: string
  kind: 'plan_changed' | 'usage_warning' | 'usage_limit_reached' | 'usage_summary'
  period: string | null
  created_at: string
  provider: string
  delivery: string
  details: Record<string, unknown>
}

export interface Billing {
  plan: AiPlan
  plans: BillingPlan[]
  limit: Limit
  provider: { name: string; live: boolean; note: string }
  events: BillingEvent[]
}

export interface PlanChangePreview {
  current: AiPlan
  target: string
  allowed: boolean
  blockers: string[]
  effects: ({ code: string } & Record<string, unknown>)[]
}

export interface SummaryLine {
  code: string
  description: string
  quantity: number | null
  unit: string
}

export interface UsageSummary {
  workspace: { id: string; name: string }
  period: { month: string; start: string; end: string; timezone: string }
  plan: { key: AiPlan; name: string }
  plan_changes: { at: string; from: string | null; to: string | null }[]
  lines: SummaryLine[]
  currency: string
  amount_due_inr: number
  price_status: string
  status: string
  notes: string[]
  /** When the finished month was frozen, or null while it is still a live draft. */
  frozen_at: string | null
  /** The month has not ended yet in the workspace's time zone (M19). */
  in_progress: boolean
}

const query = (month?: string) => (month ? `?month=${encodeURIComponent(month)}` : '')

export const billingApi = {
  get: () => authedRequest<Billing>('/org/billing'),
  preview: (plan: AiPlan) =>
    authedRequest<PlanChangePreview>('/org/billing/plan-change/preview', {
      method: 'POST',
      body: { plan },
    }),
  confirm: (plan: AiPlan) =>
    authedRequest<Billing>('/org/billing/plan', { method: 'PUT', body: { plan } }),
  summary: (month?: string) => authedRequest<UsageSummary>(`/org/billing/summary${query(month)}`),
  summaryCsv: (month?: string) => authedDownload(`/org/billing/summary.csv${query(month)}`),
}
