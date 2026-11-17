import type {
  Connection,
  ConnectionDetail,
  ConnectionList,
  PersonAccess,
  Preview,
} from '../lib/connectionsApi'

// UrbanCart's connections as the API returns them. Dates are more than a week old, so screens show fixed dates
// ("30 Oct 2026") and screenshots stay stable. Shared by tests, stories and Playwright.

const quality = {
  stale: {
    kind: 'stale',
    severity: 'warning' as const,
    message: 'The newest row is from 30 Sep 2026, 40 days ago. Recent activity may be missing.',
    column: 'created_at',
    details: { newest: '2026-09-30', age_days: 40 },
  },
  missingWebsite: {
    kind: 'missing_segment',
    severity: 'warning' as const,
    message: 'Rows with sales_channel = Website look missing for the week of 9 Mar 2026.',
    column: 'sales_channel',
    details: { value: 'Website', period_start: '2026-03-09', rows: 0, usual_rows: 95 },
  },
  totalRow: {
    kind: 'total_row_dropped',
    severity: 'info' as const,
    message: "The 'Total' row at the end was left out, so totals are not counted twice.",
    column: null,
    details: {},
  },
}

export const demoOrders: Connection = {
  id: 'c1',
  type: 'file',
  name: 'UrbanCart orders',
  status: 'active',
  sync_state: 'idle',
  last_success_at: '2026-10-30T04:30:00+00:00',
  last_error: null,
  created_at: '2026-10-21T05:00:00+00:00',
  created_by_name: 'Asha Rao',
  datasets_count: 1,
  rows_total: 28540,
  warnings_count: 2,
  file: {
    version: 2,
    original_name: 'shopify_orders.csv',
    size_bytes: 6_247_368,
    uploaded_at: '2026-10-30T04:25:00+00:00',
    latest_upload_failed: false,
    latest_upload_error: null,
    latest_upload_needs_review: false,
    latest_upload_version: null,
  },
  google: null,
}

export const demoMarketing: Connection = {
  id: 'c2',
  type: 'google_sheets',
  name: 'Google Sheets (asha.urbancart@gmail.com)',
  status: 'active',
  sync_state: 'idle',
  last_success_at: '2026-10-29T11:05:00+00:00',
  last_error: null,
  created_at: '2026-10-22T05:00:00+00:00',
  created_by_name: 'Asha Rao',
  datasets_count: 8,
  rows_total: 126,
  warnings_count: 0,
  file: null,
  google: {
    account_email: 'asha.urbancart@gmail.com',
    spreadsheets: [{ id: 'sheet-marketing', name: 'UrbanCart marketing plan' }],
  },
}

export const demoTargets: Connection = {
  id: 'c3',
  type: 'google_sheets',
  name: 'Regional targets',
  status: 'needs_reauth',
  sync_state: 'idle',
  last_success_at: '2026-10-25T06:00:00+00:00',
  last_error: 'Google stopped sharing data. Sign in to Google again to reconnect.',
  created_at: '2026-10-22T06:00:00+00:00',
  created_by_name: 'Meera Shah',
  datasets_count: 1,
  rows_total: 84,
  warnings_count: 0,
  file: null,
  google: {
    account_email: 'meera.urbancart@gmail.com',
    spreadsheets: [{ id: 'sheet-targets', name: 'Targets 2026' }],
  },
}

export const demoRazorpay: Connection = {
  id: 'c4',
  type: 'razorpay',
  name: 'Razorpay',
  status: 'active',
  sync_state: 'idle',
  last_success_at: '2026-10-30T04:30:00+00:00',
  last_error: null,
  created_at: '2026-10-30T04:25:00+00:00',
  created_by_name: 'Asha Rao',
  datasets_count: 4,
  rows_total: 18_203,
  warnings_count: 0,
  file: null,
  google: null,
  razorpay: { key_id_masked: 'rzp_live_AbCd••••', mode: 'live' },
}

export const demoShopify: Connection = {
  id: 'c5',
  type: 'shopify',
  name: 'Shopify (UrbanCart)',
  status: 'active',
  sync_state: 'idle',
  last_success_at: '2026-10-30T04:30:00+00:00',
  last_error: null,
  created_at: '2026-10-30T04:25:00+00:00',
  created_by_name: 'Asha Rao',
  datasets_count: 5,
  rows_total: 60_212,
  warnings_count: 0,
  file: null,
  google: null,
  shopify: {
    shop: 'urbancart.myshopify.com',
    shop_name: 'UrbanCart',
    currency: 'INR',
    all_orders: false,
  },
}

export const demoConnectionList: ConnectionList = {
  connections: [demoOrders, demoMarketing, demoTargets],
  sources: {
    upload_max_mb: 50,
    google_sheets: { available: true, stand_in: false },
    razorpay: { available: true, stand_in: false },
    shopify: { available: true, stand_in: false },
  },
}

export const demoOrdersDetail: ConnectionDetail = {
  ...demoOrders,
  // Version 3 differs from version 2 (in use), so it waits for the Owner (DECISIONS D58).
  file: {
    ...demoOrders.file!,
    latest_upload_needs_review: true,
    latest_upload_version: 3,
  },
  datasets: [
    {
      id: 'd1',
      name: 'shopify_orders',
      row_count: 28540,
      columns: [
        { name: 'id', label: 'id', type: 'integer', unit: null, date_format: null },
        {
          name: 'created_at',
          label: 'created_at',
          type: 'datetime',
          unit: null,
          date_format: 'ISO 8601',
        },
        {
          name: 'sales_channel',
          label: 'sales_channel',
          type: 'text',
          unit: null,
          date_format: null,
        },
        {
          name: 'product_type',
          label: 'product_type',
          type: 'text',
          unit: null,
          date_format: null,
        },
        { name: 'quantity', label: 'quantity', type: 'integer', unit: null, date_format: null },
        {
          name: 'total_price',
          label: 'total_price',
          type: 'number',
          unit: null,
          date_format: null,
        },
        {
          name: 'shipping_region',
          label: 'shipping_region',
          type: 'text',
          unit: null,
          date_format: null,
        },
      ],
      last_fetched_at: '2026-10-30T04:30:00+00:00',
      quality: [quality.stale, quality.missingWebsite, quality.totalRow],
      last_error: null,
    },
  ],
  versions: [
    {
      id: 'v3',
      version: 3,
      original_name: 'orders_export_nov.csv',
      size_bytes: 1_204_550,
      status: 'needs_confirmation',
      error: null,
      uploaded_at: '2026-10-31T06:10:00+00:00',
      uploaded_by_name: 'Asha Rao',
      in_use: false,
      changes: [
        {
          kind: 'columns_missing',
          sheet: 'shopify_orders',
          columns: ['sales_channel', 'shipping_region'],
          count: 2,
        },
      ],
    },
    {
      id: 'v2',
      version: 2,
      original_name: 'shopify_orders.csv',
      size_bytes: 6_247_368,
      status: 'ready',
      error: null,
      uploaded_at: '2026-10-30T04:25:00+00:00',
      uploaded_by_name: 'Asha Rao',
      in_use: true,
      changes: [],
    },
    {
      id: 'v1',
      version: 1,
      original_name: 'orders_october.csv',
      size_bytes: 5_904_112,
      status: 'ready',
      error: null,
      uploaded_at: '2026-10-21T05:00:00+00:00',
      uploaded_by_name: 'Asha Rao',
      in_use: false,
      changes: [],
    },
  ],
}

export const demoDatasetPreview: Preview = {
  dataset_id: 'd1',
  columns: demoOrdersDetail.datasets[0]!.columns,
  rows: [
    [5000000001, '2025-01-01T00:25:18', 'Marketplace', 'Home & Kitchen', 1, 644.86, 'North'],
    [5000000002, '2025-01-01T02:02:09', 'App', 'Electronics', 1, 2322.4, 'East'],
    [5000000003, '2025-01-01T03:41:55', 'Website', 'Fashion', 2, 1871.62, 'South'],
    [5000000004, '2025-01-01T05:12:40', 'App', 'Beauty', 3, null, 'West'],
  ],
  row_count: 28540,
  fetched_at: '2026-10-30T04:30:00+00:00',
  checked_at: '2026-10-30T04:30:00+00:00',
  source: 'cache',
  quality: [quality.stale, quality.missingWebsite, quality.totalRow],
}

export const demoAccess: PersonAccess[] = [
  {
    membership_id: 'm1',
    user_id: 'u1',
    name: 'Asha Rao',
    email: 'asha@urbancart.in',
    role: 'owner',
    access: 'owner',
    connection_ids: [],
  },
  {
    membership_id: 'm2',
    user_id: 'u2',
    name: 'Ravi Kumar',
    email: 'ravi@urbancart.in',
    role: 'analyst',
    access: 'selected',
    connection_ids: ['c1'],
  },
  {
    membership_id: 'm3',
    user_id: 'u3',
    name: 'Lata Menon',
    email: 'lata@urbancart.in',
    role: 'viewer',
    access: 'all',
    connection_ids: [],
  },
]
