import type { DictionaryContent } from '../../lib/dictionaryApi'

// The same rules as the API's ``app/dictionary/labels.py`` (M19, DECISIONS D127), worked out here because the
// draft is edited on this screen: removing "revenue" makes "sum_total_price" a metric of its own again at once.

const KNOWN: Record<string, string> = {
  aov: 'Average order value',
  revenue: 'Revenue',
  orders: 'Orders',
}
const RAW = /^[a-z0-9]+(?:_[a-z0-9]+)+$/

function words(name: string): string {
  const text = name.replace(/[_\s]+/g, ' ').trim()
  return text ? text[0]!.toUpperCase() + text.slice(1) : name
}

function lowerFirst(text: string): string {
  return text.length === 1 || /[a-z]/.test(text[1] ?? '')
    ? text[0]!.toLowerCase() + text.slice(1)
    : text
}

function formula(expr: string | undefined): string {
  return (expr ?? '').replace(/\s+/g, '').toLowerCase()
}

/** "Total fee" for ``sum_fee``, "Number of orders" for ``orders_count``, else the key in words. */
export function metricLabel(content: DictionaryContent, key: string): string {
  const known = KNOWN[key]
  if (known) return known
  const metric = content.metrics[key]
  const table = metric?.dataset ? content.datasets[metric.dataset] : undefined
  if (metric && table) {
    const expr = formula(metric.expr)
    if (key.startsWith('sum_') && expr === `sum(${key.slice(4).toLowerCase()})`) {
      const column = table.columns[key.slice(4)]
      const raw = column?.label || key.slice(4)
      const label = RAW.test(raw) || raw === key.slice(4) ? words(raw) : raw
      return label.toLowerCase().startsWith('total') ? label : `Total ${lowerFirst(label)}`
    }
    if (key.endsWith('_count') && expr === 'count(*)')
      return `Number of ${lowerFirst(words(table.name))}`
  }
  return words(key)
}

/** ``{ duplicate: the metric it repeats }``: drafted-by-code metrics with another metric's table and formula. */
export function duplicateMetrics(content: DictionaryContent): Record<string, string> {
  const kept = new Map<string, string>()
  for (const [key, metric] of Object.entries(content.metrics)) {
    const mark = `${metric.dataset ?? ''}|${formula(metric.expr)}`
    if (metric.source !== 'code' && !kept.has(mark)) kept.set(mark, key)
  }
  const out: Record<string, string> = {}
  for (const [key, metric] of Object.entries(content.metrics)) {
    if (metric.source !== 'code') continue
    const other = kept.get(`${metric.dataset ?? ''}|${formula(metric.expr)}`)
    if (other && other !== key) out[key] = other
  }
  return out
}
