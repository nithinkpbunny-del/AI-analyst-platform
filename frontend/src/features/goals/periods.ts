import { isoDay } from '../../lib/goalFormat'

export type Preset = 'this_month' | 'next_month' | 'this_quarter' | 'custom'
export const PRESETS: Preset[] = ['this_month', 'next_month', 'this_quarter', 'custom']

/** The dates of a preset period around `today` ("2026-11-20"): calendar month or quarter. */
export function presetPeriod(preset: Preset, today: string): { start: string; end: string } {
  const [year = 2026, month = 1] = today.split('-').map(Number)
  const first = (y: number, m: number) => new Date(Date.UTC(y, m - 1, 1))
  const last = (y: number, m: number) => new Date(Date.UTC(y, m, 0))
  if (preset === 'next_month') {
    const next = first(year, month + 1)
    const y = next.getUTCFullYear()
    const m = next.getUTCMonth() + 1
    return { start: isoDay(first(y, m)), end: isoDay(last(y, m)) }
  }
  if (preset === 'this_quarter') {
    const startMonth = Math.floor((month - 1) / 3) * 3 + 1
    return { start: isoDay(first(year, startMonth)), end: isoDay(last(year, startMonth + 2)) }
  }
  return { start: isoDay(first(year, month)), end: isoDay(last(year, month)) }
}
