// Shared number formatting (BUILD_SPEC 11): ₹8,450 · ₹12.4 L · ₹1.24 Cr; percentages to 1 decimal.

const MINUS = '−' // typographic minus, easier to read than a hyphen next to numbers
const LAKH = 1e5
const CRORE = 1e7

const indianGrouping = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

function trimZeros(value: string): string {
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value
}

function sign(value: number): string {
  return value < 0 ? MINUS : ''
}

/** Indian-style rupee amount. Below ₹1 lakh: full digits; then lakhs (1 decimal); then crores (2 decimals). */
export function formatINR(value: number, options: { full?: boolean } = {}): string {
  if (!Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (options.full || Math.round(abs) < LAKH) {
    return `${sign(value)}₹${indianGrouping.format(Math.round(abs))}`
  }
  const lakhs = Math.round((abs / LAKH) * 10) / 10
  if (lakhs < 100) return `${sign(value)}₹${trimZeros(lakhs.toFixed(1))} L`
  const crores = Math.round((abs / CRORE) * 100) / 100
  return `${sign(value)}₹${trimZeros(crores.toFixed(2))} Cr`
}

/** Plain count with Indian digit grouping: 12,40,000. */
export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return `${sign(value)}${indianGrouping.format(Math.round(Math.abs(value)))}`
}

/** Percentage with one decimal. `value` is already in percent (12.4 means 12.4%). */
export function formatPct(value: number, options: { signed?: boolean } = {}): string {
  if (!Number.isFinite(value)) return '—'
  const rounded = Math.round(Math.abs(value) * 10) / 10
  const prefix = value < 0 && rounded !== 0 ? MINUS : options.signed && rounded !== 0 ? '+' : ''
  return `${prefix}${rounded.toFixed(1)}%`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "just now", "2 min ago", "3 h ago", "yesterday", "4 days ago", then a date such as "12 Mar 2026". */
export function formatRelativeTime(from: Date | string, now: Date = new Date()): string {
  const date = typeof from === 'string' ? new Date(from) : from
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000)
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days} days ago`
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`
}

/** "9 Nov 2026" in the given time zone (default: the device's). */
export function formatDate(value: Date | string, timeZone?: string): string {
  const date = typeof value === 'string' ? new Date(value) : value
  return new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone,
  }).format(date)
}

/** "9 Nov 2026, 2:30 pm" in the given time zone (default: the device's). */
export function formatDateTime(value: Date | string, timeZone?: string): string {
  const date = typeof value === 'string' ? new Date(value) : value
  const parts = new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    timeZone,
  }).formatToParts(date)
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? ''
  const period = get('dayPeriod').toLowerCase()
  return `${get('day')} ${get('month')} ${get('year')}, ${get('hour')}:${get('minute')} ${period}`
}
