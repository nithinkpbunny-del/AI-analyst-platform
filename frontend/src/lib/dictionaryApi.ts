import { API_BASE_URL, ApiError, authedRequest, getAccessToken } from './api'

// Shapes as the API sends them (backend app/dictionary/model.py, app/schemas/dictionary.py).
export type DictionaryRole = 'key' | 'time' | 'measure' | 'dimension' | 'detail'
export type DictionaryUnit = 'INR' | 'percent'
export type DictionarySource = 'code' | 'ai' | 'owner' | 'carried'
export const ROLES: DictionaryRole[] = ['key', 'time', 'measure', 'dimension', 'detail']
export const INDUSTRIES = [
  'retail',
  'd2c',
  'restaurant',
  'fuel_station',
  'services',
  'manufacturing',
  'other',
] as const
export type Industry = (typeof INDUSTRIES)[number]

export interface DictionaryColumn {
  label: string
  type: string
  meaning: string
  role: DictionaryRole
  unit: DictionaryUnit | null
  /** The stored number divided by this gives the unit (Razorpay paise: 100 gives rupees). */
  divide_by: 100 | null
  synonyms: string[]
  joins: string[]
  evidence: string[]
  needs_review: boolean
  review_reason: string | null
  source: DictionarySource
}

export interface DictionaryTable {
  dataset_id: string | null
  connection_id: string | null
  connection_name: string
  name: string
  meaning: string
  grain: string
  time_column: string | null
  columns: Record<string, DictionaryColumn>
}

export interface DictionaryMetric {
  expr: string
  dataset: string | null
  unit: DictionaryUnit | 'count' | null
  good_direction: 'up' | 'down' | null
  meaning: string
  source: DictionarySource
  /** null: not checked yet. */
  valid: boolean | null
  /** "code" or "code: detail", e.g. "unknown_column: total_prce". */
  error: string | null
  needs_review: boolean
}

export interface DictionaryJoin {
  left: string
  right: string
  type: 'many_to_one' | 'one_to_one' | 'many_to_many'
  match_rate: number | null
  source: DictionarySource
  needs_review: boolean
}

export interface DictionaryContent {
  company: { name: string; industry: Industry; currency: 'INR'; fiscal_year_start: string }
  datasets: Record<string, DictionaryTable>
  metrics: Record<string, DictionaryMetric>
  relationships: DictionaryJoin[]
}

/** What changed against the dictionary a draft continues. */
export interface DictionaryDiffItem {
  kind: string
  dataset?: string
  column?: string
  columns?: number
  from?: string
  to?: string
  meaning?: string
  metric?: string
  metrics?: string[]
}

export interface DictionaryNotes {
  /** ok | cached | skipped | unreadable | unavailable:<code> | failed:<kind> */
  ai?: string
  dropped_metrics?: { name: string; error: string }[]
  dropped_joins?: number
  unreadable_tables?: string[]
}

export interface DictionaryVersion {
  id: string
  version: number
  status: 'draft' | 'confirmed' | 'superseded'
  origin: 'initial' | 'schema_change' | 'replacement' | 'redraft'
  created_at: string
  confirmed_at: string | null
  confirmed_by_name: string | null
  review_items: number
  diff: DictionaryDiffItem[]
  notes: DictionaryNotes
}

export interface DictionaryVersionContent extends DictionaryVersion {
  content: DictionaryContent
}

export interface DictionaryState {
  /** False until an Owner confirms a version: answers then say "dictionary not confirmed". */
  confirmed: boolean
  drafting: boolean
  active: DictionaryVersionContent | null
  draft: DictionaryVersionContent | null
  versions: DictionaryVersion[]
}

export interface ReplaceResult {
  matched: [string, string][]
  unmatched: string[]
  removed_old: boolean
  draft: DictionaryVersionContent
}

export const DICTIONARY = ['dictionary'] as const

export const dictionaryApi = {
  get: () => authedRequest<DictionaryState>('/dictionary'),
  save: (content: DictionaryContent) =>
    authedRequest<DictionaryVersionContent>('/dictionary/draft', { method: 'PUT', body: content }),
  requestDraft: (freshAi: boolean) =>
    authedRequest<{ drafting: boolean }>('/dictionary/draft', {
      method: 'POST',
      body: { fresh_ai: freshAi },
    }),
  discard: () => authedRequest<void>('/dictionary/draft', { method: 'DELETE' }),
  confirm: () =>
    authedRequest<DictionaryVersionContent>('/dictionary/draft/confirm', { method: 'POST' }),
  replace: (fromId: string, toId: string, removeOld: boolean) =>
    authedRequest<ReplaceResult>('/dictionary/replace', {
      method: 'POST',
      body: { from_connection_id: fromId, to_connection_id: toId, remove_old: removeOld },
    }),
}

/** Download a version as YAML. The file needs the access token, so it is fetched and handed over as a blob. */
export async function downloadYaml(versionId?: string): Promise<void> {
  const query = versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''
  const fetchOnce = () =>
    fetch(`${API_BASE_URL}/dictionary/export.yaml${query}`, {
      headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
      credentials: 'include',
    })
  let response = await fetchOnce()
  if (response.status === 401) {
    await authedRequest('/auth/me').catch(() => undefined)
    response = await fetchOnce()
  }
  if (!response.ok) {
    throw new ApiError(response.status, 'export_failed', 'The export did not work. Try again.')
  }
  const blob = await response.blob()
  const disposition = response.headers.get('content-disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'dictionary.yaml'
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
