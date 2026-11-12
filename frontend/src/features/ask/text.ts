import type { TFunction } from 'i18next'
import type { AnswerNote, ProgressStep } from '../../lib/chatApi'

/** i18next uses ":" for namespaces, so API codes like "llm:quota" become "llm_quota" keys. */
export function codeKey(code: string): string {
  return code.replace(/[^a-z0-9]+/gi, '_')
}

export function progressText(t: TFunction, step: ProgressStep): string {
  if (step.code === 'computing' && step.metric) {
    return t('ask.progress.computingMetric', { metric: step.metric.replace(/_/g, ' ') })
  }
  return t(`ask.progress.${step.code}`, {
    defaultValue: t('ask.progress.working'),
    table: step.table ?? '',
    connection: step.connection ?? '',
  })
}

export function errorText(t: TFunction, code: string | null): string {
  const key = codeKey(code ?? 'internal')
  return t(`ask.errors.${key}`, { defaultValue: t('ask.errors.unknown', { code: code ?? '' }) })
}

/** The fix an error needs: ask again, connect data or open Settings. */
export function errorAction(code: string | null): 'askAgain' | 'connect' | 'settings' {
  if (!code) return 'askAgain'
  if (code === 'no_sources') return 'connect'
  if (code.startsWith('ai:') || code === 'llm:invalid_key' || code === 'llm:model_not_found') {
    return 'settings'
  }
  return 'askAgain'
}

export function noteText(t: TFunction, note: AnswerNote): string {
  const detail = note.detail ?? ''
  const [date, ...rest] = detail.split(':')
  return t(`ask.notes.${codeKey(note.code)}`, {
    defaultValue: t('ask.notes.unknown', { code: note.code }),
    detail: detail.replace(/:/g, ' '),
    date,
    period: rest.join(':') || detail,
    metrics: (note.metrics ?? []).join(', '),
  })
}

export function toolLabel(t: TFunction, tool: string): string {
  return t(`ask.tools.${tool}`, { defaultValue: tool })
}
