import { describe, expect, it } from 'vitest'
import { formatINR, formatNumber, formatPct, formatRelativeTime } from './format'

describe('formatINR', () => {
  it.each([
    [8450, '₹8,450'],
    [0, '₹0'],
    [99_999, '₹99,999'],
    [1_00_000, '₹1 L'],
    [12_40_000, '₹12.4 L'],
    [12_44_999, '₹12.4 L'],
    [99_94_999, '₹99.9 L'],
    [99_96_000, '₹1 Cr'],
    [1_24_00_000, '₹1.24 Cr'],
    [1_20_00_000, '₹1.2 Cr'],
    [3_45_67_89_000, '₹345.68 Cr'],
    [-12_40_000, '−₹12.4 L'],
    [-512.4, '−₹512'],
  ])('%d → %s', (value, expected) => {
    expect(formatINR(value)).toBe(expected)
  })

  it('can show the full amount with Indian grouping', () => {
    expect(formatINR(12_40_000, { full: true })).toBe('₹12,40,000')
  })

  it('shows a dash for missing numbers', () => {
    expect(formatINR(Number.NaN)).toBe('—')
  })
})

describe('formatNumber', () => {
  it('uses lakh grouping', () => {
    expect(formatNumber(1240000)).toBe('12,40,000')
    expect(formatNumber(-1500)).toBe('−1,500')
  })
})

describe('formatPct', () => {
  it.each([
    [12.44, {}, '12.4%'],
    [12.45, { signed: true }, '+12.5%'],
    [-3.06, {}, '−3.1%'],
    [0, { signed: true }, '0.0%'],
    [-0.01, {}, '0.0%'],
  ])('%d %o → %s', (value, options, expected) => {
    expect(formatPct(value, options)).toBe(expected)
  })
})

describe('formatRelativeTime', () => {
  const now = new Date('2026-11-09T10:00:00Z')
  it.each([
    ['2026-11-09T09:59:30Z', 'just now'],
    ['2026-11-09T09:58:00Z', '2 min ago'],
    ['2026-11-09T07:00:00Z', '3 h ago'],
    ['2026-11-08T08:00:00Z', 'yesterday'],
    ['2026-11-05T10:00:00Z', '4 days ago'],
    ['2026-03-12T10:00:00Z', '12 Mar 2026'],
  ])('%s → %s', (from, expected) => {
    expect(formatRelativeTime(from, now)).toBe(expected)
  })
})
