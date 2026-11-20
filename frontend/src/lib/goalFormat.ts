import { formatDate, formatINR, formatNumber, formatPct } from './format'

/** A figure in its metric's unit: ₹ amounts Indian-style, counts with Indian grouping (M15 goals and tiles). */
export function formatValue(
  value: number | null | undefined,
  unit: string | null | undefined,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (unit === 'INR') return formatINR(value)
  if (unit === 'pct') return formatPct(value)
  if (unit === 'count') return formatNumber(value)
  return Math.abs(value) >= 100
    ? formatNumber(value)
    : String(Math.round(value * 100) / 100).replace('-', '−')
}

/** A calendar day sent as "2026-09-01", shown as "1 Sept 2026" whatever the device's time zone. */
export function formatDay(day: string): string {
  return formatDate(`${day.slice(0, 10)}T00:00:00Z`, 'UTC')
}

/** "September 2026" for a whole calendar month, else "1 Jul – 30 Sept 2026". */
export function formatPeriod(start: string, end: string): string {
  const a = new Date(`${start.slice(0, 10)}T00:00:00Z`)
  const b = new Date(`${end.slice(0, 10)}T00:00:00Z`)
  const lastDay = new Date(Date.UTC(b.getUTCFullYear(), b.getUTCMonth() + 1, 0)).getUTCDate()
  if (
    a.getUTCDate() === 1 &&
    a.getUTCFullYear() === b.getUTCFullYear() &&
    a.getUTCMonth() === b.getUTCMonth() &&
    b.getUTCDate() === lastDay
  ) {
    return new Intl.DateTimeFormat('en-IN', {
      month: 'long',
      year: 'numeric',
      timeZone: 'UTC',
    }).format(a)
  }
  const short = new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  })
  return `${short.format(a)} – ${formatDay(end)}`
}

/** ISO "YYYY-MM-DD" of a UTC date. */
export function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10)
}
