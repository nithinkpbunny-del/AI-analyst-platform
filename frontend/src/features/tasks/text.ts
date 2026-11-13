import type { TFunction } from 'i18next'

/** Why a long task did not finish, in words. */
export function taskReason(t: TFunction, code: string | null): string {
  if (!code) return t('tasks.reasons.generic')
  if (code.startsWith('ai:') || code.startsWith('llm:')) return t('tasks.reasons.ai')
  return t(`tasks.reasons.${code.replace(':', '_')}`, { defaultValue: t('tasks.reasons.generic') })
}
