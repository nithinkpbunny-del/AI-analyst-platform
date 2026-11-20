import type { Dashboard, GoalList, GoalOptions, Tile } from '../lib/goalsApi'

/** M15 fixtures from the UrbanCart demo company, mid-November 2026. */
const goalProvenance = {
  result_id: 'r1',
  tool: 'goal_progress',
  tables: [
    {
      table: 'orders',
      name: 'Orders',
      connection: 'Shopify',
      fetched_at: '2026-11-16T03:00:00Z',
      rows: 28540,
    },
  ],
  inputs: {},
  sql: [],
  row_count: 2,
  fetched_at: '2026-11-16T03:00:00Z',
}

export const demoGoalList: GoalList = {
  computed_at: '2026-10-30T04:30:00Z',
  refreshing: false,
  stale: false,
  error: null,
  provenance: { r1: goalProvenance },
  quality: [],
  notes: [],
  goals: [
    {
      id: 'goal-nov',
      name: 'November sales',
      metric: 'revenue',
      metric_label: 'Revenue',
      unit: 'INR',
      target: 5000000,
      direction: 'at_least',
      period_start: '2026-11-01',
      period_end: '2026-11-30',
      filters: {},
      created_by: 'Asha Rao',
      created_at: '2026-11-01T04:00:00Z',
      progress: {
        result_id: 'r1',
        status: 'behind',
        as_of: '2026-11-15',
        elapsed_days: 15,
        total_days: 30,
        elapsed_pct: 50,
        actual: 1840000,
        actual_pct_of_target: 36.8,
        run_rate_projection: 3680000,
        seasonal_projection: 3480000,
        projection: 3480000,
        projected_pct_of_target: 69.6,
        gap: -1520000,
        needed_per_day: 210666.67,
        method: 'seasonal',
      },
    },
    {
      id: 'goal-refunds',
      name: null,
      metric: 'refunds',
      metric_label: 'Refunds',
      unit: 'INR',
      target: 150000,
      direction: 'at_most',
      period_start: '2026-11-01',
      period_end: '2026-11-30',
      filters: { shipping_region: 'South' },
      created_by: 'Ravi Kumar',
      created_at: '2026-11-02T04:00:00Z',
      progress: {
        result_id: 'r1',
        status: 'on_track',
        as_of: '2026-11-15',
        elapsed_days: 15,
        total_days: 30,
        elapsed_pct: 50,
        actual: 52000,
        actual_pct_of_target: 34.7,
        run_rate_projection: 104000,
        seasonal_projection: null,
        projection: 104000,
        projected_pct_of_target: 69.3,
        gap: -46000,
        needed_per_day: null,
        method: 'run_rate',
      },
    },
  ],
}

export const demoGoalOptions: GoalOptions = {
  today: '2026-11-16',
  metrics: [
    {
      key: 'revenue',
      label: 'Revenue',
      unit: 'INR',
      good_direction: 'up',
      direction: 'at_least',
      additive: true,
      dimensions: [
        { key: 'shipping_region', label: 'Region' },
        { key: 'sales_channel', label: 'Sales channel' },
      ],
    },
    {
      key: 'refunds',
      label: 'Refunds',
      unit: 'INR',
      good_direction: 'down',
      direction: 'at_most',
      additive: true,
      dimensions: [],
    },
  ],
}

const kpiProvenance = {
  ...goalProvenance,
  result_id: 'r2',
  tool: 'kpi_summary',
  row_count: 1,
}

export function demoTile(overrides: Partial<Tile> = {}): Tile {
  return {
    id: 'tile-1',
    title: 'Revenue last month',
    mine: true,
    owner_name: null,
    shared: false,
    refresh_minutes: 60,
    dates: 'rolling',
    fixed_sql: false,
    question: 'How did revenue do last month?',
    conversation_id: 'conv-1',
    message_id: 'msg-2',
    created_at: '2026-11-10T04:00:00Z',
    refreshing: false,
    stale: false,
    result: {
      status: 'ok',
      computed_at: '2026-10-30T04:30:00Z',
      headline: {
        result_id: 'r2',
        value: 4468291,
        format: 'inr',
        delta_pct: 12.4,
        good_direction: 'up',
        label: 'revenue',
      },
      charts: [],
      table: {
        result_id: 'r2',
        columns: ['metric', 'current', 'previous', 'change_pct'],
        rows: [['revenue', 4468291, 3975000, 12.4]],
        total_rows: 1,
      },
      provenance: { r2: kpiProvenance },
      quality: [],
      notes: [],
    },
    ...overrides,
  }
}

export const demoDashboard: Dashboard = {
  can_edit: true,
  limit: 30,
  tiles: [
    demoTile(),
    demoTile({
      id: 'tile-2',
      title: 'Orders by channel',
      result: {
        status: 'error',
        error: 'out_of_date',
        computed_at: '2026-10-30T04:30:00Z',
      },
    }),
    demoTile({
      id: 'tile-team',
      title: 'South region this quarter',
      mine: false,
      owner_name: 'Ravi Kumar',
      shared: true,
      conversation_id: null,
      message_id: null,
      result: null,
    }),
  ],
}
