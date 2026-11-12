import type {
  DictionaryColumn,
  DictionaryContent,
  DictionaryState,
  DictionaryVersion,
  DictionaryVersionContent,
} from '../lib/dictionaryApi'

function column(
  type: string,
  role: DictionaryColumn['role'],
  meaning: string,
  extra: Partial<DictionaryColumn> = {},
): DictionaryColumn {
  return {
    label: meaning,
    type,
    meaning,
    role,
    unit: null,
    divide_by: null,
    synonyms: [],
    joins: [],
    evidence: [],
    needs_review: false,
    review_reason: null,
    source: 'code',
    ...extra,
  }
}

/** The demo orders (no ₹ in the file, only a currency column) and Razorpay payments (paise). */
export function demoDictionaryContent(): DictionaryContent {
  return {
    company: { name: 'UrbanCart', industry: 'd2c', currency: 'INR', fiscal_year_start: '04-01' },
    datasets: {
      shopify_orders: {
        dataset_id: 'd1',
        connection_id: 'c1',
        connection_name: 'UrbanCart orders',
        name: 'shopify_orders',
        meaning: 'One row per customer order',
        grain: 'order',
        time_column: 'created_at',
        columns: {
          id: column('integer', 'key', 'Order id'),
          created_at: column('datetime', 'time', 'When the order was placed'),
          customer_id: column('integer', 'key', 'Customer', { joins: ['shopify_customers.id'] }),
          total_price: column('number', 'measure', 'Order value incl. tax', {
            unit: 'INR',
            evidence: ['money_name', 'currency_column'],
            synonyms: ['revenue', 'sales'],
            source: 'ai',
          }),
        },
      },
      razorpay_payments: {
        dataset_id: 'd2',
        connection_id: 'c2',
        connection_name: 'Razorpay export',
        name: 'razorpay_payments',
        meaning: 'One row per payment',
        grain: 'payment',
        time_column: null,
        columns: {
          id: column('text', 'key', 'Payment id'),
          amount: column('integer', 'measure', 'Amount paid', {
            unit: 'INR',
            divide_by: 100,
            evidence: ['money_name', 'currency_column', 'paise_signature'],
            needs_review: true,
            review_reason: 'paise',
          }),
          fee: column('integer', 'measure', 'Gateway fee', {
            unit: 'INR',
            divide_by: 100,
            evidence: ['money_name', 'currency_column', 'paise_signature'],
            needs_review: true,
            review_reason: 'paise',
          }),
        },
      },
    },
    metrics: {
      revenue: {
        expr: 'SUM(total_price) - SUM(total_refunds)',
        dataset: 'shopify_orders',
        unit: 'INR',
        good_direction: 'up',
        meaning: 'Sales after refunds',
        source: 'ai',
        valid: true,
        error: null,
        needs_review: false,
      },
      margin: {
        expr: 'SUM(total_price) - SUM(cost)',
        dataset: 'shopify_orders',
        unit: 'INR',
        good_direction: null,
        meaning: '',
        source: 'owner',
        valid: false,
        error: 'unknown_column: Referenced column "cost" not found',
        needs_review: false,
      },
    },
    relationships: [
      {
        left: 'shopify_orders.customer_id',
        right: 'shopify_customers.id',
        type: 'many_to_one',
        match_rate: 1,
        source: 'code',
        needs_review: false,
      },
    ],
  }
}

/** The same dictionary with everything checked and the broken metric gone: ready to confirm. */
export function checkedDictionaryContent(): DictionaryContent {
  const content = demoDictionaryContent()
  for (const table of Object.values(content.datasets)) {
    for (const item of Object.values(table.columns)) {
      item.needs_review = false
      item.review_reason = null
    }
  }
  delete content.metrics.margin
  return content
}

export function version(
  status: 'draft' | 'confirmed' | 'superseded',
  content: DictionaryContent,
  extra: Partial<DictionaryVersionContent> = {},
): DictionaryVersionContent {
  const review = Object.values(content.datasets)
    .flatMap((table) => Object.values(table.columns))
    .filter((item) => item.needs_review).length
  return {
    id: `v-${status}-${extra.version ?? 1}`,
    version: 1,
    status,
    origin: 'initial',
    created_at: '2026-11-10T04:30:00+00:00',
    confirmed_at: status === 'draft' ? null : '2026-11-10T05:30:00+00:00',
    confirmed_by_name: status === 'draft' ? null : 'Asha Rao',
    review_items: review + (content.metrics.margin ? 1 : 0),
    diff: [],
    notes: { ai: 'ok' },
    content,
    ...extra,
  }
}

export const firstDraftState: DictionaryState = {
  confirmed: false,
  drafting: false,
  active: null,
  draft: version('draft', demoDictionaryContent()),
  versions: [],
}

/** A version as the history list shows it: without its content. */
function row(full: DictionaryVersionContent): DictionaryVersion {
  return Object.fromEntries(
    Object.entries(full).filter(([key]) => key !== 'content'),
  ) as unknown as DictionaryVersion
}

export const confirmedState: DictionaryState = {
  confirmed: true,
  drafting: false,
  active: version('confirmed', checkedDictionaryContent()),
  draft: null,
  versions: [row(version('confirmed', checkedDictionaryContent()))],
}

export const confirmedWithDraftState: DictionaryState = {
  ...confirmedState,
  draft: version('draft', checkedDictionaryContent(), {
    version: 2,
    origin: 'schema_change',
    diff: [
      { kind: 'column_added', dataset: 'shopify_orders', column: 'coupon' },
      { kind: 'column_removed', dataset: 'shopify_orders', column: 'sales_channel' },
    ],
  }),
}
