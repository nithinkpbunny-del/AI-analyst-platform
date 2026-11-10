import type { TFunction } from 'i18next'

/** TanStack Query keys. Invalidating CONNECTIONS also refreshes every connection's detail and the access list. */
export const CONNECTIONS = ['connections'] as const
export const ACCESS = ['connections', 'access'] as const

const ALLOWED = /\.(xlsx|csv|tsv|txt)$/i

/** Check a chosen file before sending it: type and size. Returns the problem, or null. */
export function fileProblem(file: File, maxMb: number, t: TFunction): string | null {
  if (!ALLOWED.test(file.name)) return t('connections.add.wrongType')
  if (file.size > maxMb * 1024 * 1024) return t('connections.add.tooLarge', { mb: maxMb })
  return null
}
