import './strings'
import type { TFunction } from 'i18next'
import { ApiError } from '../../lib/api'
import type {
  DictionaryContent,
  DictionaryDiffItem,
  DictionaryNotes,
  DictionaryVersion,
} from '../../lib/dictionaryApi'

export function reasonText(t: TFunction, reason: string | null): string {
  return reason ? t(`dictionary.reasons.${reason}`, { defaultValue: '' }) : ''
}

export function evidenceText(t: TFunction, evidence: string[]): string {
  const items = evidence
    .map((code) => t(`dictionary.evidence.${code}`, { defaultValue: '' }))
    .filter(Boolean)
  return items.length ? t('dictionary.evidenceLead', { items: items.join(', ') }) : ''
}

/** "unknown_column: total_prce" -> the sentence, plus the engine's detail for people who need it. */
export function metricErrorText(
  t: TFunction,
  error: string | null,
): { text: string; detail: string } {
  if (!error) return { text: '', detail: '' }
  const [code, ...rest] = error.split(': ')
  return {
    text: t(`dictionary.metrics.errors.${code}`, { defaultValue: t('dictionary.errors.generic') }),
    detail: rest.join(': '),
  }
}

export function changeText(t: TFunction, item: DictionaryDiffItem): string {
  const metrics = Array.isArray(item.metrics) ? item.metrics.map(String).filter(Boolean) : []
  let kind = item.kind
  if (kind === 'metrics_moved') {
    if (metrics.length === 0) kind = 'metrics_moved_unnamed'
    else if (item.from === item.to) kind = 'metrics_moved_same'
  }
  return t(`dictionary.changes.${kind}`, {
    defaultValue: '',
    dataset: item.dataset ?? '',
    column: item.column ?? '',
    from: item.from ?? '',
    to: item.to ?? '',
    meaning: item.meaning ?? '',
    metric: item.metric ?? '',
    metrics: metrics.join(', '),
  })
}

/** How the AI step went, as a sentence. */
export function aiNote(t: TFunction, notes: DictionaryNotes): string {
  const status = notes.ai
  if (!status) return ''
  const [kind, code] = status.split(':')
  if (kind === 'failed') {
    return t(`dictionary.ai.failed.${code}`, { defaultValue: t('dictionary.ai.failed.other') })
  }
  if (kind === 'unavailable') return t('dictionary.ai.unavailable')
  return t(`dictionary.ai.${kind}`, { defaultValue: '' })
}

/** Items that still wait for the Owner: flagged columns, metrics that are flagged or broken, weak joins. */
export function pendingItems(content: DictionaryContent): number {
  let count = 0
  for (const table of Object.values(content.datasets)) {
    for (const column of Object.values(table.columns)) if (column.needs_review) count += 1
  }
  for (const metric of Object.values(content.metrics)) {
    if (metric.needs_review || metric.valid === false) count += 1
  }
  for (const join of content.relationships) if (join.needs_review) count += 1
  return count
}

export function columnCount(content: DictionaryContent): number {
  return Object.values(content.datasets).reduce(
    (n, table) => n + Object.keys(table.columns).length,
    0,
  )
}

export function versionLine(t: TFunction, version: DictionaryVersion, date: string): string {
  return version.confirmed_by_name
    ? t('dictionary.confirmedLine', {
        version: version.version,
        name: version.confirmed_by_name,
        date,
      })
    : t('dictionary.confirmedLineNoName', { version: version.version, date })
}

export type Notice = { tone: 'success' | 'error'; text: string } | null

export function errorText(t: TFunction, error: unknown): string {
  if (error instanceof ApiError) {
    return t(`dictionary.errors.${error.code}`, { defaultValue: error.message })
  }
  return t('dictionary.errors.generic')
}
