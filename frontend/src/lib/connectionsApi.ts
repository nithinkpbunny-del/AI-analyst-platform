import { API_BASE_URL, ApiError, authedRequest, getAccessToken, NETWORK_ERROR_CODE } from './api'
import type { Role } from './role'

// Shapes as the API sends them (backend app/schemas/connections.py).
export type ConnectionType =
  'file' | 'google_sheets' | 'razorpay' | 'shopify' | 'zoho_books' | 'sql_database'
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
  /** Razorpay: the key id is shown masked; the secret is never returned. */
  razorpay?: { key_id_masked: string; mode: 'live' | 'test' } | null
  /** Shopify: the store's address (not a secret); the access token is never returned. */
  shopify?: ShopifyStore | null
  /** Zoho Books: the organisation read and Zoho's data centre; the refresh token is never returned. */
  zoho?: ZohoBooks | null
  /** PostgreSQL or MySQL: where it is and which tables are used; the password is never returned. */
  database?: DatabaseInfo | null
}

export type DatabaseEngine = 'postgresql' | 'mysql'
export type SslMode = 'verify-full' | 'require' | 'disable'

export interface DatabaseInfo {
  engine: DatabaseEngine
  host: string
  port: number
  database: string
  username_masked: string
  ssl_mode: SslMode
  tables: string[]
  /** The connection check found that the user can change data (the session is read-only anyway). */
  can_write: boolean
  write_reasons: string[]
  server_version: string | null
}

export interface DatabaseRequest {
  engine: DatabaseEngine
  host: string
  port?: number
  database: string
  username: string
  password: string
  ssl_mode: SslMode
  name?: string
}

export interface DatabaseTable {
  ref: string
  name: string
  kind: 'table' | 'view'
  rows_estimate: number | null
  chosen: boolean
}

export interface ZohoBooks {
  region: string
  region_name: string
  organization_id: string
  organization_name: string | null
  currency: string | null
  /** Other organisations the signed-in Zoho account can see. */
  organizations: { id: string; name: string }[]
  /** Zoho API calls today for the organisation (on the connection page only). */
  calls_today?: number | null
  daily_limit: number
}

export interface ShopifyStore {
  shop: string
  shop_name: string | null
  currency: string | null
  /** Shopify's "all orders" access: without it only the last 60 days of orders can be read. */
  all_orders: boolean
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
  razorpay: { available: boolean; stand_in: boolean }
  shopify: { available: boolean; stand_in: boolean }
  zoho_books?: { available: boolean; stand_in: boolean; region: string }
  database?: { available: boolean; ssl_off_allowed: boolean; demo_hosts: string[] }
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

export interface RazorpayKey {
  key_id: string
  key_secret: string
  name?: string
}

export interface ShopifyFinish {
  connection: Connection
  reconnected: boolean
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
  /** Start connecting a store (`shop`), or signing in again for a connection. Returns Shopify's approval page. */
  shopifyStart: (target: { shop: string } | { connection_id: string }) =>
    authedRequest<{ authorization_url: string }>('/connections/shopify/start', {
      method: 'POST',
      body: target,
    }),
  shopifyFinish: (state: string, code: string) =>
    authedRequest<ShopifyFinish>('/connections/shopify/finish', {
      method: 'POST',
      body: { state, code },
    }),
  /** Start connecting Zoho Books, or signing in again for a connection. Returns Zoho's consent page. */
  zohoStart: (connectionId?: string) =>
    authedRequest<{ authorization_url: string }>('/connections/zoho/start', {
      method: 'POST',
      body: connectionId ? { connection_id: connectionId } : {},
    }),
  zohoFinish: (state: string, code: string) =>
    authedRequest<ShopifyFinish>('/connections/zoho/finish', {
      method: 'POST',
      body: { state, code },
    }),
  zohoOrganization: (connectionId: string, organizationId: string) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}/zoho/organization`, {
      method: 'PUT',
      body: { organization_id: organizationId },
    }),
  addDatabase: (body: DatabaseRequest) =>
    authedRequest<Connection>('/connections/database', { method: 'POST', body }),
  replaceDatabaseCredentials: (connectionId: string, body: DatabaseRequest) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}/database/credentials`, {
      method: 'PUT',
      body,
    }),
  databaseTables: (connectionId: string) =>
    authedRequest<{ tables: DatabaseTable[]; max_tables: number }>(
      `/connections/${id(connectionId)}/database/tables`,
    ),
  chooseDatabaseTables: (connectionId: string, tables: string[]) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}/database/tables`, {
      method: 'PUT',
      body: { tables },
    }),
  addRazorpay: (body: RazorpayKey) =>
    authedRequest<Connection>('/connections/razorpay', { method: 'POST', body }),
  replaceRazorpayKey: (connectionId: string, body: RazorpayKey) =>
    authedRequest<Connection>(`/connections/${id(connectionId)}/razorpay/key`, {
      method: 'PUT',
      body,
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
