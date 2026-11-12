import type { TFunction } from 'i18next'
import { ApiError } from '../../lib/api'
import { formatINR, formatNumber, formatPct } from '../../lib/format'
import type { Assumption, Lever, SideEffect, WhatIfFacts, WhatIfRequest } from '../../lib/whatIfApi'

export const ELASTICITY_KEY: Record<Lever, string> = {
  price: 'price_elasticity',
  discount: 'discount_elasticity',
  marketing_spend: 'spend_elasticity',
}

/** The slider for each lever: price and spend in percent, discount in percentage points. */
export const LEVER_RANGE: Record<Lever, { min: number; max: number; step: number; start: number }> =
  {
    price: { min: -30, max: 30, step: 1, start: 10 },
    discount: { min: -10, max: 15, step: 1, start: 5 },
    marketing_spend: { min: -50, max: 100, step: 5, start: 20 },
  }

const MINUS = '−'

export function signed(value: number, unit: 'INR' | 'count' | null): string {
  const rounded = unit === 'INR' ? formatINR(Math.abs(value)) : formatNumber(Math.abs(value))
  if (Math.round(Math.abs(value)) === 0) return rounded
  return `${value < 0 ? MINUS : '+'}${rounded}`
}

export function money(value: number, unit: 'INR' | 'count' | null): string {
  return unit === 'INR' ? formatINR(value) : formatNumber(value)
}

export function leverValue(t: TFunction, lever: Lever, value: number): string {
  const text = `${value < 0 ? MINUS : value > 0 ? '+' : ''}${Math.abs(value)}`
  return t(`whatIfPage.changeValue.${lever}`, {
    value: lever === 'discount' ? text : `${text}%`,
  })
}

export function scenarioTitle(t: TFunction, request: WhatIfRequest, scopeText: string): string {
  const change = `${request.change < 0 ? MINUS : request.change > 0 ? '+' : ''}${Math.abs(request.change)}${
    request.lever === 'discount' ? '' : '%'
  }`
  const base = t(`whatIfPage.title_scenario.${request.lever}`, {
    change,
    channel: request.scope?.channel ?? '',
  })
  return scopeText && request.lever !== 'marketing_spend'
    ? `${base} ${t('whatIfPage.in', { scope: scopeText })}`
    : base
}

/** The headline sentence: the expected change and its range, never one without the other. */
export function headline(t: TFunction, facts: WhatIfFacts): { lead: string; figure: string } {
  const months = t('whatIfPage.headline.months', { count: facts.horizon_months })
  return {
    lead: t('whatIfPage.headline.lead', { months }),
    figure: signed(facts.delta_expected, facts.unit),
  }
}

export function rangeText(t: TFunction, facts: WhatIfFacts): string {
  return t('whatIfPage.headline.range', {
    low: signed(facts.delta_low, facts.unit),
    high: signed(facts.delta_high, facts.unit),
    range: facts.range,
  })
}

export function straddlesZero(facts: WhatIfFacts): boolean {
  return facts.delta_low < 0 && facts.delta_high > 0
}

function plain(value: number): string {
  const rounded = Math.round(value * 10) / 10
  return `${rounded < 0 ? MINUS : ''}${Math.abs(rounded)}`
}

function date(value: string | null): string {
  return value ?? ''
}

export function assumptionText(t: TFunction, facts: WhatIfFacts, item: Assumption): string {
  const informed = facts.informed_by
  switch (item.key) {
    case 'price_elasticity':
    case 'discount_elasticity':
    case 'spend_elasticity': {
      const value = typeof item.value === 'number' ? item.value : 0
      const why = item.reason ? t(`whatIfPage.why.${item.reason}`, { defaultValue: '' }) : ''
      return t(`whatIfPage.assumption.${item.key}.${item.source}`, {
        value: plain(value),
        low: plain(item.low ?? value),
        high: plain(item.high ?? value),
        weeks: informed.weeks ?? 0,
        start: date(informed.start),
        end: date(informed.end),
        rows: informed.campaign_rows ?? 0,
        why: why || t('whatIfPage.why.default_assumption'),
      })
    }
    case 'current_discount_rate':
      return t('whatIfPage.assumption.current_discount_rate', {
        value: plain(typeof item.value === 'number' ? item.value : 0),
      })
    case 'baseline_method':
      return t('whatIfPage.assumption.baseline_method', {
        method: t(`whatIfPage.method.${String(item.value)}`, { defaultValue: String(item.value) }),
      })
    default:
      return t(`whatIfPage.assumption.${item.key}`, { defaultValue: '' })
  }
}

export function confidenceReason(t: TFunction, reason: string): string {
  return t(`whatIfPage.confidenceReason.${reason}`, { defaultValue: '' })
}

export function effectText(t: TFunction, effect: SideEffect, unit: 'INR' | 'count' | null): string {
  switch (effect.code) {
    case 'units_change':
      return t('whatIfPage.effects.units_change', {
        pct: formatPct(effect.pct ?? 0, { signed: true }),
      })
    case 'discount_given':
      return t('whatIfPage.effects.discount_given', {
        before: money(effect.before ?? 0, unit),
        after: money(effect.after ?? 0, unit),
      })
    case 'repeat_exposure':
      return t('whatIfPage.effects.repeat_exposure', {
        share: formatPct(effect.share_pct ?? 0),
      })
    case 'extra_spend':
      return t('whatIfPage.effects.extra_spend', { amount: signed(effect.amount ?? 0, 'INR') })
  }
}

export function errorText(t: TFunction, error: unknown): string {
  if (error instanceof ApiError) {
    return t(`whatIfPage.errors.${error.code}`, { defaultValue: t('whatIfPage.errors.generic') })
  }
  return t('whatIfPage.errors.generic')
}
