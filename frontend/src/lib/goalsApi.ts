import { authedRequest } from './api'
import type { ChartSpec, QualityWarning, ResultProvenance } from './chatApi'

// M15: goals (targets with progress) and the pinned dashboard. Only the lazily loaded screens import this file.

const id = encodeURIComponent

// ---------------------------------------------------------------------------------------------- goals
export type GoalDirection = 'at_least' | 'at_most'
export type GoalStatus =
  'not_started' | 'on_track' | 'at_risk' | 'behind' | 'achieved' | 'missed' | 'no_data'

/** Computed by code for the person looking (BUILD_SPEC X7); every figure is in `result_id`'s result. */
export interface GoalProgress {
  result_id: string
  status: GoalStatus
  as_of: string | null
  elapsed_days: number
  total_days: number
  elapsed_pct: number
  actual: number | null
  actual_pct_of_target: number | null
  run_rate_projection: number | null
  seasonal_projection: number | null
  projection: number | null
  projected_pct_of_target: number | null
  gap: number | null
  needed_per_day: number | null
  method: 'final' | 'seasonal' | 'run_rate' | 'current_value' | null
}

export interface Goal {
  id: string
  name: string | null
  metric: string
  metric_label: string
  unit: string | null
  target: number
  direction: GoalDirection
  period_start: string
  period_end: string
  filters: Record<string, string>
  created_by: string | null
  created_at: string
  progress: GoalProgress | null
}

export interface GoalList {
  goals: Goal[]
  computed_at: string | null
  refreshing: boolean
  stale: boolean
  error: string | null
  provenance: Record<string, ResultProvenance>
  quality: QualityWarning[]
  notes: string[]
}

export interface MetricOption {
  key: string
  label: string
  unit: string | null
  good_direction: 'up' | 'down' | null
  direction: GoalDirection
  additive: boolean
  dimensions: { key: string; label: string }[]
}

export interface GoalOptions {
  metrics: MetricOption[]
  today: string
}

export interface GoalInput {
  metric: string
  target: number
  period_start: string
  period_end: string
  direction?: GoalDirection
  name?: string | null
  filters?: Record<string, string>
}

// ------------------------------------------------------------------------------------------ dashboard
export type TileDates = 'rolling' | 'fixed'
export const REFRESH_CHOICES = [15, 60, 240, 1440] as const
export type RefreshMinutes = (typeof REFRESH_CHOICES)[number]

export interface TileHeadline {
  result_id: string
  value: number
  format: 'inr' | 'pct' | 'number'
  delta_pct: number | null
  good_direction: 'up' | 'down' | null
  label: string | null
}

export interface TileResult {
  status: 'ok' | 'error'
  error?: string
  computed_at: string
  headline?: TileHeadline | null
  charts?: ChartSpec[]
  table?: {
    result_id: string
    columns: string[]
    rows: (string | number | boolean | null)[][]
    total_rows?: number
  } | null
  provenance?: Record<string, ResultProvenance>
  quality?: QualityWarning[]
  notes?: string[]
}

export interface Tile {
  id: string
  title: string
  mine: boolean
  owner_name: string | null
  shared: boolean
  refresh_minutes: number
  dates: TileDates
  fixed_sql: boolean
  question: string
  conversation_id: string | null
  message_id: string | null
  created_at: string
  result: TileResult | null
  refreshing: boolean
  stale: boolean
}

export interface Dashboard {
  tiles: Tile[]
  can_edit: boolean
  limit: number
}

export interface TileChanges {
  title?: string
  shared?: boolean
  refresh_minutes?: RefreshMinutes
  dates?: TileDates
}

export const goalsApi = {
  list: () => authedRequest<GoalList>('/goals'),
  refresh: (force = false) =>
    authedRequest<GoalList>('/goals/refresh', { method: 'POST', body: { force } }),
  options: () => authedRequest<GoalOptions>('/goals/options'),
  create: (input: GoalInput) => authedRequest<Goal>('/goals', { method: 'POST', body: input }),
  update: (goalId: string, changes: Partial<GoalInput>) =>
    authedRequest<Goal>(`/goals/${id(goalId)}`, { method: 'PATCH', body: changes }),
  remove: (goalId: string) => authedRequest<void>(`/goals/${id(goalId)}`, { method: 'DELETE' }),
}

export const dashboardApi = {
  get: () => authedRequest<Dashboard>('/dashboard'),
  refresh: (options: { force?: boolean; tileIds?: string[] } = {}) =>
    authedRequest<Dashboard>('/dashboard/refresh', {
      method: 'POST',
      body: {
        force: options.force ?? false,
        ...(options.tileIds ? { tile_ids: options.tileIds } : {}),
      },
    }),
  pin: (messageId: string, title: string | null, dates: TileDates) =>
    authedRequest<Tile>('/dashboard/tiles', {
      method: 'POST',
      body: { message_id: messageId, title, dates },
    }),
  update: (tileId: string, changes: TileChanges) =>
    authedRequest<Tile>(`/dashboard/tiles/${id(tileId)}`, { method: 'PATCH', body: changes }),
  remove: (tileId: string) =>
    authedRequest<void>(`/dashboard/tiles/${id(tileId)}`, { method: 'DELETE' }),
  reorder: (tileIds: string[]) =>
    authedRequest<Dashboard>('/dashboard/order', { method: 'PUT', body: { tile_ids: tileIds } }),
}
