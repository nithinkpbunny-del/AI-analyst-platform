import { authedRequest } from './api'
import type { SourceTable } from './chatApi'

export type Lever = 'price' | 'discount' | 'marketing_spend'
export type Confidence = 'high' | 'medium' | 'low'

export interface WhatIfDimension {
  table: string
  column: string
  label: string
}

export interface WhatIfMetric {
  key: string
  label: string
  unit: 'INR' | 'count' | null
  levers: Lever[]
  dimensions: WhatIfDimension[]
}

export interface WhatIfOptions {
  metrics: WhatIfMetric[]
  /** The table and column that hold the marketing channels, when spend by channel is connected. */
  channels: { table: string; column: string } | null
  industry: string | null
  max_horizon: number
}

export interface WhatIfRequest {
  lever: Lever
  change: number
  metric?: string
  scope?: Record<string, string>
  horizon?: number
  /** `price_elasticity`, `discount_elasticity` or `spend_elasticity`. */
  assumptions?: Record<string, number>
}

export type AssumptionSource = 'history' | 'default' | 'you'

export interface Assumption {
  key: string
  value: number | string | boolean | null
  low?: number
  high?: number
  source: AssumptionSource
  editable: boolean
  reason?: string | null
  t_value?: number | null
}

export interface SideEffect {
  code: 'units_change' | 'discount_given' | 'repeat_exposure' | 'extra_spend'
  pct?: number
  before?: number
  after?: number
  share_pct?: number
  amount?: number
}

export interface WhatIfFacts {
  scenario: {
    metric: string
    lever: Lever
    change: number
    scope: Record<string, string>
    horizon: number
    overrides: Record<string, number>
  }
  metric: string
  unit: 'INR' | 'count' | null
  channel?: string
  horizon_months: number
  baseline_method: string | null
  baseline_total: number
  baseline_low: number
  baseline_high: number
  scenario_total: number
  scenario_low: number
  scenario_high: number
  delta_expected: number
  delta_low: number
  delta_high: number
  delta_pct_expected: number
  delta_pct_low: number
  delta_pct_high: number
  units_change_pct: number | null
  range: string
  assumptions: Assumption[]
  confidence: { level: Confidence; reason: string }
  informed_by: {
    source: AssumptionSource
    weeks: number | null
    start: string | null
    end: string | null
    campaign_rows?: number
  }
  side_effects: SideEffect[]
  monthly_spend?: number
  extra_spend?: number
  net_after_spend?: number
  current_roas?: number | null
  backtest_mape_pct: number | null
}

export interface WhatIfResult {
  facts: WhatIfFacts
  columns: string[]
  rows: (string | number | null)[][]
  notes: string[]
  tables: SourceTable[]
  sql: string[]
  quality_warnings: { code: string }[]
}

export const whatIfApi = {
  options: () => authedRequest<WhatIfOptions>('/whatif/options'),
  values: (table: string, column: string) =>
    authedRequest<{ values: string[] }>(
      `/whatif/values?table=${encodeURIComponent(table)}&column=${encodeURIComponent(column)}`,
    ).then((r) => r.values),
  run: (request: WhatIfRequest) =>
    authedRequest<WhatIfResult>('/whatif/run', { method: 'POST', body: request }),
}
