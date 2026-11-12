import type { WhatIfFacts, WhatIfOptions, WhatIfResult } from '../lib/whatIfApi'

export const demoOptions: WhatIfOptions = {
  metrics: [
    {
      key: 'revenue',
      label: 'revenue',
      unit: 'INR',
      levers: ['price', 'discount'],
      dimensions: [
        { table: 'shopify_orders', column: 'product_type', label: 'Product type' },
        { table: 'shopify_orders', column: 'shipping_region', label: 'Shipping region' },
      ],
    },
  ],
  channels: { table: 'q3_2026', column: 'channel' },
  industry: 'd2c',
  max_horizon: 6,
}

export function demoFacts(overrides: Partial<WhatIfFacts> = {}): WhatIfFacts {
  return {
    scenario: {
      metric: 'revenue',
      lever: 'price',
      change: 10,
      scope: { product_type: 'Electronics' },
      horizon: 3,
      overrides: {},
    },
    metric: 'revenue',
    unit: 'INR',
    horizon_months: 3,
    baseline_method: 'trend_x_seasonal_index',
    baseline_total: 6_388_516,
    baseline_low: 5_800_000,
    baseline_high: 7_000_000,
    scenario_total: 6_149_544,
    scenario_low: 5_300_000,
    scenario_high: 6_900_000,
    delta_expected: -238_972,
    delta_low: -635_865,
    delta_high: 185_304,
    delta_pct_expected: -3.7,
    delta_pct_low: -10,
    delta_pct_high: 2.9,
    units_change_pct: -12.5,
    range: '80%',
    assumptions: [
      {
        key: 'price_elasticity',
        value: -1.4,
        low: -2.1,
        high: -0.7,
        source: 'default',
        editable: true,
        reason: 'history_inconclusive',
        t_value: null,
      },
      {
        key: 'baseline_method',
        value: 'trend_x_seasonal_index',
        source: 'history',
        editable: false,
      },
      { key: 'nothing_else_changes', value: true, source: 'default', editable: false },
    ],
    confidence: { level: 'low', reason: 'history_inconclusive' },
    informed_by: { source: 'default', weeks: 91, start: null, end: null },
    side_effects: [
      { code: 'units_change', pct: -12.5 },
      { code: 'repeat_exposure', share_pct: 81.7 },
    ],
    backtest_mape_pct: 8.2,
    ...overrides,
  }
}

export function demoResult(overrides: Partial<WhatIfResult> = {}): WhatIfResult {
  return {
    facts: demoFacts(),
    columns: [
      'bucket',
      'baseline',
      'baseline_low',
      'baseline_high',
      'scenario',
      'scenario_low',
      'scenario_high',
    ],
    rows: [
      ['2026-10-01', 2_000_000, 1_800_000, 2_200_000, 1_920_000, 1_650_000, 2_150_000],
      ['2026-11-01', 2_200_000, 1_950_000, 2_450_000, 2_110_000, 1_800_000, 2_380_000],
      ['2026-12-01', 2_188_516, 1_900_000, 2_450_000, 2_119_544, 1_850_000, 2_370_000],
    ],
    notes: ['what_if_assumptions', 'what_if_default_assumption'],
    tables: [
      {
        table: 'shopify_orders',
        name: 'shopify_orders',
        connection: 'Shopify orders',
        fetched_at: new Date(Date.now() - 2 * 60_000).toISOString(),
      },
    ],
    sql: ['SELECT 1'],
    quality_warnings: [],
    ...overrides,
  }
}
