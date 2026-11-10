import type { TFunction } from 'i18next'
import type { Column, Connection, QualityIssue, VersionChange } from '../../lib/connectionsApi'
import { formatDate, formatNumber } from '../../lib/format'

const str = (value: unknown): string =>
  typeof value === 'string' || typeof value === 'number' ? String(value) : ''
const num = (value: unknown): string =>
  typeof value === 'number' ? formatNumber(value) : str(value)

function monthLabel(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`)
  return new Intl.DateTimeFormat('en-IN', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date)
}

function day(iso: unknown): string {
  return typeof iso === 'string' ? formatDate(`${iso}T00:00:00Z`, 'UTC') : ''
}

/** A data note in the web app's words (locales/en.ts), from its kind and numbers; the API's sentence otherwise. */
export function qualityText(t: TFunction, issue: QualityIssue): string {
  const d = issue.details
  switch (issue.kind) {
    case 'incomplete_period':
      return d.grain === 'month'
        ? t('connections.quality.incomplete_month', {
            month: typeof d.period_start === 'string' ? monthLabel(d.period_start) : '',
            rows: num(d.rows),
            share: str(d.share_pct),
          })
        : t('connections.quality.incomplete_week', {
            date: day(d.period_start),
            rows: num(d.rows),
            share: str(d.share_pct),
          })
    case 'missing_segment':
      return t('connections.quality.missing_segment', {
        column: issue.column ?? '',
        value: str(d.value),
        date: day(d.period_start),
        rows: num(d.rows),
        usual: num(d.usual_rows),
      })
    case 'stale':
      return t('connections.quality.stale', { date: day(d.newest), days: num(d.age_days) })
    case 'nulls':
      return t('connections.quality.nulls', { share: str(d.share_pct), label: str(d.label) })
    case 'empty_column':
      return t('connections.quality.empty_column', { label: str(d.label) })
    case 'duplicate_key':
      return t('connections.quality.duplicate_key', { count: num(d.repeated), label: str(d.label) })
    case 'duplicate_rows':
      return t('connections.quality.duplicate_rows', { count: num(d.rows) })
    case 'currency_mismatch':
      return t('connections.quality.currency_mismatch', {
        label: str(d.label),
        currencies: Array.isArray(d.currencies) ? d.currencies.join(', ') : '',
      })
    case 'unparsed_values':
      return t('connections.quality.unparsed_values', {
        count: num(d.count),
        label: str(d.label),
        expected: t(`connections.quality.unparsedExpected.${str(d.expected) || 'values'}`),
      })
    case 'truncated':
      return t('connections.quality.truncated', { rows: num(d.rows) })
    case 'total_row_dropped':
      return t('connections.quality.total_row_dropped')
    default:
      return issue.message
  }
}

export function columnType(t: TFunction, column: Column): string {
  const type = t(`connections.columnTypes.${column.type}`, { defaultValue: column.type })
  return column.date_format && column.date_format !== 'ISO 8601'
    ? `${type}, ${t('connections.readAs', { format: column.date_format })}`
    : type
}

export function unitLabel(t: TFunction, unit: string | null): string {
  return unit ? t(`connections.units.${unit}`, { defaultValue: unit }) : ''
}

export function summary(t: TFunction, connection: Connection): string {
  if (!connection.datasets_count) return t('connections.noTables')
  return t('connections.summary', {
    count: connection.datasets_count,
    rows: formatNumber(connection.rows_total),
  })
}

export function typeLabel(t: TFunction, connection: Pick<Connection, 'type'>): string {
  return t(`connections.types.${connection.type}`)
}

/** "640 KB", "12.4 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const ID_LIKE = /(^id$|_id$|^.+id$|_no$|number$|_code$)/

/** A preview cell as text: dates as "5 Jan 2026", numbers with Indian grouping, empty as a dash. */
export function cellText(t: TFunction, value: unknown, column: Column | undefined): string {
  if (value === null || value === undefined || value === '') return t('connections.detail.empty')
  if (typeof value === 'boolean')
    return value ? t('connections.detail.yes') : t('connections.detail.no')
  if (typeof value === 'number') {
    // Ids and codes are labels, not quantities: no digit grouping.
    if (column && ID_LIKE.test(column.name)) return String(value)
    if (column?.type === 'number' && !Number.isInteger(value)) {
      return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 4 }).format(value)
    }
    return formatNumber(value)
  }
  if (typeof value === 'string' && column?.type === 'date' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return formatDate(`${value}T00:00:00Z`, 'UTC')
  }
  if (typeof value === 'string' && column?.type === 'datetime') {
    const [datePart, timePart] = value.split('T')
    return timePart
      ? `${formatDate(`${datePart}T00:00:00Z`, 'UTC')} ${timePart.slice(0, 5)}`
      : value
  }
  return String(value)
}

/** One difference between a waiting file version and the version in use, in the web app's words. */
export function changeText(t: TFunction, change: VersionChange): string {
  const type = (value: string | undefined) =>
    value ? t(`connections.columnTypes.${value}`, { defaultValue: value }) : ''
  switch (change.kind) {
    case 'sheet_missing':
      return t('connections.detail.changes.sheet_missing', { sheet: change.sheet ?? '' })
    case 'sheet_empty':
      return t('connections.detail.changes.sheet_empty', {
        sheet: change.sheet ?? '',
        rows: formatNumber(change.rows_before ?? 0),
      })
    case 'columns_missing': {
      const shown = change.columns ?? []
      const more = (change.count ?? shown.length) - shown.length
      return t('connections.detail.changes.columns_missing', {
        sheet: change.sheet ?? '',
        count: change.count ?? shown.length,
        columns: more > 0 ? `${shown.join(', ')}, …` : shown.join(', '),
      })
    }
    case 'type_changed':
      return t('connections.detail.changes.type_changed', {
        sheet: change.sheet ?? '',
        column: change.column ?? '',
        from: type(change.from),
        to: type(change.to),
      })
    default:
      return change.kind
  }
}
