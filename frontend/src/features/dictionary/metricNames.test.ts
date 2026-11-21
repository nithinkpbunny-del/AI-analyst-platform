import { describe, expect, it } from 'vitest'
import type { DictionaryContent, DictionaryMetric } from '../../lib/dictionaryApi'
import { duplicateMetrics, metricLabel } from './metricNames'

function metric(expr: string, source: DictionaryMetric['source'] = 'code'): DictionaryMetric {
  return {
    expr,
    dataset: 'shopify_orders',
    unit: 'INR',
    good_direction: 'up',
    meaning: '',
    source,
    valid: true,
    error: null,
    needs_review: false,
  }
}

const content = {
  company: { name: 'UrbanCart', industry: 'd2c', currency: 'INR', fiscal_year_start: '04-01' },
  datasets: {
    shopify_orders: {
      dataset_id: null,
      connection_id: null,
      connection_name: '',
      name: 'shopify_orders',
      meaning: '',
      grain: '',
      time_column: 'created_at',
      columns: {
        fee: { label: 'Fee', type: 'DOUBLE', meaning: '', role: 'measure', unit: 'INR' },
        total_price: {
          label: 'total_price',
          type: 'DOUBLE',
          meaning: '',
          role: 'measure',
          unit: 'INR',
        },
      },
    },
  },
  metrics: {
    shopify_orders_count: metric('COUNT(*)'),
    sum_fee: metric('SUM(fee)'),
    sum_total_price: metric('SUM(total_price)'),
    revenue: metric('sum( total_price )', 'ai'),
  },
  relationships: [],
} as unknown as DictionaryContent

describe('metric names in words (M19)', () => {
  it('reads drafted keys as names', () => {
    expect(metricLabel(content, 'sum_fee')).toBe('Total fee')
    expect(metricLabel(content, 'sum_total_price')).toBe('Total price')
    expect(metricLabel(content, 'shopify_orders_count')).toBe('Number of shopify orders')
    expect(metricLabel(content, 'gross_margin')).toBe('Gross margin')
  })

  it('finds drafted metrics that repeat the AI’s, and only those', () => {
    expect(duplicateMetrics(content)).toEqual({ sum_total_price: 'revenue' })
    const withoutRevenue = { ...content, metrics: { ...content.metrics } }
    delete withoutRevenue.metrics.revenue
    expect(duplicateMetrics(withoutRevenue)).toEqual({})
  })
})
