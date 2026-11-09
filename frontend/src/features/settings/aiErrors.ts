import type { TFunction } from 'i18next'
import type { AiProvider } from '../../lib/aiApi'
import { ApiError } from '../../lib/api'

/** Plain words for an API error: our own text for known codes (all in en.ts), the server's otherwise. */
export function aiErrorText(t: TFunction, error: unknown, provider?: AiProvider): string {
  if (error instanceof ApiError) {
    const key = `ai.errors.${error.code}`
    const providerName = provider ? t(`ai.providers.${provider}`) : t('ai.providers.gemini')
    return t(key, { defaultValue: error.message, provider: providerName })
  }
  return error instanceof Error ? error.message : String(error)
}
