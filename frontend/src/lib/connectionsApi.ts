import { API_BASE_URL, ApiError, authedRequest, getAccessToken, NETWORK_ERROR_CODE } from './api'
import type { Role } from './role'

// Shapes as the API sends them (backend app/schemas/connections.py).
export type ConnectionType = 'file' | 'google_sheets'
export type ConnectionStatus = 'active' | 'error' | 'needs_reauth' | 'disabled'
export type SyncState = 'idle' | 'queued' | 'running'
export type ColumnType = 'boolean' | 'integer' | 'number' | 'date' | 'datetime' | 'text'

export interface QualityIssue {
  kind: string
  severity: 'warning' | 'info'
  message: string
  column: string | null
  details: Record<string, unknown>
}

export interface Column {
  name: string
  label: string
  type: ColumnType | string
  unit: string | null
  date_format: string | null
}

export interface Dataset {
  id: string
  name: string
  row_count: number | null
  columns: Column[]
  last_fetched_at: string | null
  quality: QualityIssue[]
  last_error: string | null
}

export interface FileInfo {
  version: number
  original_name: string
  size_bytes: number
  uploaded_at: string
  latest_upload_failed: boolean
  latest_upload_error: string | null
  /** The newest upload differs from the version in use and waits for an Owner. */
  latest_upload_needs_review: boolean
  latest_upload_version: number | null
}

/** How a waiting version differs from the one in use (backend `version_changes`). */
export interface VersionChange {
  kind: 'sheet_missing' | 'sheet_empty' | 'columns_missing' | 'type_changed' | string
  sheet?: string
  columns?: string[]
  count?: number
  column?: string
  from?: string
  to?: string
  rows_before?: number
}

export interface FileVersion {
  id: string
  version: number
  original_name: string
  size_bytes: number
  status: 'processing' | 'ready' | 'failed' | 'needs_confirmation'
  error: string | null
  uploaded_at: string
  uploaded_by_name: string | null
  in_use: boolean
  changes: VersionChange[]
}

export interface Spreadsheet {
  id: string
  name: string
}

export interface Connection {
  id: string
  type: ConnectionType
  name: string
  status: ConnectionStatus
  sync_state: SyncState
  last_success_at: string | null
  last_error: string | null
  created_at: string
  created_by_name: string | null
  datasets_count: number
  rows_total: number
  warnings_count: number
  file: FileInfo | null
  google: { account_email: string | null; spreadsheets: Spreadsheet[] } | null
}

/** What removing a connection does to the business dictionary (shown before removing it). */
export interface DictionaryImpact {
  /** Tables whose metrics another source's table of the same name takes over. */
  moves: { table: string; to: string; metrics: number }[]
  /** Metrics written by the Owner or the AI that would point at nothing and wait for review. */
  orphaned_metrics: string[]
  joins: number
}

export interface ConnectionDetail extends Connection {
  datasets: Dataset[]
  versions: FileVersion[]
  dictionary_impact?: DictionaryImpact | null
}

export interface Sources {
  upload_max_mb: number
  google_sheets: { available: boolean; stand_in: boolean }
}

export interface ConnectionList {
  connections: Connection[]
  sources: Sources
}

export interface Preview {
  dataset_id: string
  columns: Column[]
  rows: unknown[][]
  row_count: number
  fetched_at: string
  checked_at: string
  source: 'cache' | 'unchanged' | 'incremental' | 'full'
  quality: QualityIssue[]
}

export interface TestResult {
  ok: boolean
  message: string
  checks: { label: string; ok: boolean }[]
}

export type AccessKind = 'owner' | 'all' | 'selected'

export interface PersonAccess {
  membership_id: string
  user_id: string
  name: string
  email: string
  role: Role
  access: AccessKind
  connection_ids: string[]
}

export interface PickerConfig {
  access_token: string
  expires_in: number
  developer_key: string
  app_id: string
  stand_in: boolean
  stand_in_files: Spreadsheet[]
}

export interface GoogleFinish {
  connection: Connection
  picker: PickerConfig
  reconnected: boolean
}

/** Is any connection still being read in the background? Then lists and details keep refreshing. */
export function isSyncing(connection: Pick<Connection, 'sync_state'>): boolean {
  return connection.sync_state !== 'idle'
}

const id = (value: string) => encodeURIComponent(value)

export const connectionsApi = {
  list: () => authedRequest<ConnectionList>('/connections'),
  get: (connectionId: string) =>
    authedRequest<ConnectionDetail>(`/connections/${id(connectionId)}`),
  update: (connectionId: string, changes: { name?: string; paused?: boolean }) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}`, {
      method: 'PATCH',
      body: changes,
    }),
  remove: (connectionId: string) =>
    authedRequest<void>(`/connections/${id(connectionId)}`, { method: 'DELETE' }),
  test: (connectionId: string) =>
    authedRequest<TestResult>(`/connections/${id(connectionId)}/test`, { method: 'POST' }),
  refresh: (connectionId: string) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}/refresh`, { method: 'POST' }),
  chooseVersion: (connectionId: string, versionId: string) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}/versions/${id(versionId)}/use`, {
      method: 'POST',
    }),
  discardVersion: (connectionId: string, versionId: string) =>
    authedRequest<void>(`/connections/${id(connectionId)}/versions/${id(versionId)}`, {
      method: 'DELETE',
    }),
  preview: (connectionId: string, datasetId: string) =>
    authedRequest<Preview>(`/connections/${id(connectionId)}/datasets/${id(datasetId)}/preview`),

  access: () => authedRequest<{ people: PersonAccess[] }>('/connections/access'),
  setAccess: (membershipId: string, access: 'all' | 'selected', connectionIds: string[]) =>
    authedRequest<PersonAccess>(`/connections/access/${id(membershipId)}`, {
      method: 'PUT',
      body: { access, connection_ids: access === 'selected' ? connectionIds : [] },
    }),

  googleStart: (connectionId?: string) =>
    authedRequest<{ authorization_url: string }>('/connections/google/start', {
      method: 'POST',
      body: connectionId ? { connection_id: connectionId } : {},
    }),
  googleFinish: (state: string, code: string) =>
    authedRequest<GoogleFinish>('/connections/google/finish', {
      method: 'POST',
      body: { state, code },
    }),
  pickerToken: (connectionId: string) =>
    authedRequest<PickerConfig>(`/connections/${id(connectionId)}/google/picker-token`, {
      method: 'POST',
    }),
  setSpreadsheets: (connectionId: string, spreadsheetIds: string[]) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}/google/spreadsheets`, {
      method: 'PUT',
      body: { spreadsheet_ids: spreadsheetIds },
    }),
}

interface ErrorBody {
  error?: { code?: string; message?: string; fields?: Record<string, string> }
}

/**
 * Upload a file (multipart). `request()` speaks JSON only, so this sends the form itself, with the same
 * one-time refresh of an expired access token as `authedRequest`.
 */
export async function uploadFile(
  file: File,
  connectionId?: string,
  signal?: AbortSignal,
): Promise<Connection> {
  const path = connectionId ? `/connections/${id(connectionId)}/files` : '/connections/files'
  const send = () => {
    const form = new FormData()
    form.append('file', file)
    return fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: { Accept: 'application/json', Authorization: `Bearer ${getAccessToken() ?? ''}` },
      body: form,
      credentials: 'include',
      signal,
    })
  }
  let response: Response
  try {
    response = await send()
    if (response.status === 401) {
      // An expired access token: any authed call refreshes it, then try once more.
      await authedRequest('/auth/me').catch(() => undefined)
      response = await send()
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(
      0,
      NETWORK_ERROR_CODE,
      'The server could not be reached. Check your connection and try again.',
    )
  }
  if (!response.ok) {
    let body: ErrorBody = {}
    try {
      body = (await response.json()) as ErrorBody
    } catch {
      /* not JSON */
    }
    throw new ApiError(
      response.status,
      body.error?.code ?? 'upload_failed',
      body.error?.message ?? 'The upload did not work. Try again.',
      body.error?.fields ?? {},
    )
  }
  return (await response.json()) as Connection
}
