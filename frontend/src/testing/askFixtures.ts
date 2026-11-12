import type { ChatMessage, Conversation, ConversationSummary, SavedQuestion } from '../lib/chatApi'

/** The demo company's planted finding 1 (eval/fixtures/ANSWER_KEY.md), as the agent answers it. */
const fetchedAt = '2026-11-12T09:58:00Z'

export const demoQuestion: ChatMessage = {
  id: 'm1',
  role: 'user',
  content: 'Why did South revenue fall in Q3?',
  status: 'done',
  lens: 'business',
  created_at: '2026-11-12T10:00:00Z',
  finished_at: null,
  answer: null,
  progress: [],
  tool_trace: [],
  provenance: {},
  quality_warnings: [],
  error_code: null,
  reused: false,
}

export const demoAnswer: ChatMessage = {
  id: 'm2',
  role: 'assistant',
  content: 'South revenue fell 17.7% [r1] in Q3 while every other region grew.',
  status: 'done',
  lens: 'business',
  created_at: '2026-11-12T10:00:01Z',
  finished_at: '2026-11-12T10:00:19Z',
  answer: {
    lead: 'South revenue fell 17.7% [r1] in Q3 2026 while every other region grew.',
    key_figure: '17.7%',
    evidence: [
      'South: ₹28.0 L [r1] in Q3, down from ₹34.0 L [r1] in Q2',
      'West grew 18.5% [r1], East 17.0% [r1], North 10.1% [r1]',
      'Repeat customers explain 88.9% [r2] of the South drop; they ordered 21.4% [r2] less often',
      'Electronics, priced 12% higher from July, fell most in the South [r3]',
    ],
    why_it_matters:
      'The South was the largest region in Q2; at this rate Q3 misses its target by ₹9.6 L [r4].',
    recommendation: null,
    data_notes: ['Prices are list prices; discounts at checkout are not split out.'],
    notes: [{ code: 'dictionary_not_confirmed' }],
    next_questions: [
      'Which Electronics products lost the most South customers?',
      'How did repeat customers in the South behave before July?',
    ],
    charts: [
      {
        chart_id: 'c1',
        type: 'bar',
        title: 'South fell 17.7% while every other region grew',
        x: { name: 'value', values: ['South', 'West', 'East', 'North'] },
        series: [
          { name: 'previous', data: [3404299, 3009646, 2118417, 3340679] },
          { name: 'current', data: [2801265, 3565233, 2478448, 3679722] },
        ],
        result_id: 'r1',
        unit: 'INR',
      },
    ],
    confidence: 'high',
    cited: ['r1', 'r2', 'r3', 'r4'],
  },
  progress: [
    { code: 'reading_question' },
    { code: 'thinking' },
    { code: 'computing', tool: 'driver_analysis', metric: 'revenue' },
    { code: 'fetching', table: 'shopify_orders', connection: 'Shopify orders' },
    { code: 'checking_numbers' },
    { code: 'done' },
  ],
  tool_trace: [
    {
      id: 't1',
      tool: 'driver_analysis',
      input: { metric: 'revenue', dimensions: ['shipping_region'] },
      ms: 412,
      result_id: 'r1',
      sql: [
        'SELECT CAST(shipping_region AS VARCHAR) AS value, (SUM(total_price) - SUM(total_refunds)) AS metric FROM (SELECT __m.* FROM shopify_orders AS __m) AS b WHERE created_at >= ? AND created_at < ? GROUP BY 1',
      ],
      tables: ['shopify_orders'],
      rows: 4,
    },
    {
      id: 't2',
      tool: 'run_sql',
      input: { sql: 'SELECT nope FROM shopify_orders' },
      ms: 8,
      error: 'sql_error',
    },
  ],
  provenance: {
    r1: {
      result_id: 'r1',
      tool: 'driver_analysis',
      tables: [
        {
          table: 'shopify_orders',
          name: 'shopify_orders',
          connection: 'Shopify orders',
          fetched_at: fetchedAt,
          rows: 28540,
        },
      ],
      inputs: {},
      sql: [],
      row_count: 4,
      fetched_at: fetchedAt,
    },
    r2: {
      result_id: 'r2',
      tool: 'driver_analysis',
      tables: [
        {
          table: 'shopify_orders',
          name: 'shopify_orders',
          connection: 'Shopify orders',
          fetched_at: fetchedAt,
          rows: 28540,
        },
      ],
      inputs: {},
      sql: [],
      row_count: 5,
      fetched_at: fetchedAt,
    },
    r3: {
      result_id: 'r3',
      tool: 'run_sql',
      tables: [
        {
          table: 'shopify_products',
          name: 'shopify_products',
          connection: 'Shopify products',
          fetched_at: fetchedAt,
          rows: 40,
        },
      ],
      inputs: {},
      sql: [],
      row_count: 5,
      fetched_at: fetchedAt,
    },
    r4: {
      result_id: 'r4',
      tool: 'run_sql',
      tables: [
        {
          table: 'targets',
          name: 'Targets',
          connection: 'UrbanCart marketing plan',
          fetched_at: fetchedAt,
          rows: 84,
        },
      ],
      inputs: {},
      sql: [],
      row_count: 4,
      fetched_at: fetchedAt,
    },
  },
  quality_warnings: [
    {
      table: 'shopify_orders',
      name: 'shopify_orders',
      kind: 'missing_segment',
      severity: 'warning',
      message: 'Rows with sales_channel = Website look missing for the week of 9 Mar 2026.',
      column: 'sales_channel',
      details: { value: 'Website', period_start: '2026-03-09', rows: 0, usual_rows: 95 },
    },
  ],
  error_code: null,
  reused: false,
}

export const demoRunning: ChatMessage = {
  ...demoAnswer,
  id: 'm4',
  content: '',
  status: 'running',
  finished_at: null,
  answer: null,
  quality_warnings: [],
  progress: [
    { code: 'reading_question' },
    { code: 'checking_sources' },
    { code: 'fetching', table: 'shopify_orders', connection: 'Shopify orders' },
  ],
}

export const demoConversation: Conversation = {
  id: 'conv1',
  title: 'Why did South revenue fall in Q3?',
  lens: 'business',
  created_at: '2026-11-12T10:00:00Z',
  last_message_at: '2026-11-12T10:00:19Z',
  messages: [demoQuestion, demoAnswer],
}

export const demoConversations: ConversationSummary[] = [
  {
    id: 'conv1',
    title: 'Why did South revenue fall in Q3?',
    lens: 'business',
    created_at: '2026-11-12T10:00:00Z',
    last_message_at: '2026-11-12T10:00:19Z',
  },
  {
    id: 'conv2',
    title: 'Which marketing channel gives the best return?',
    lens: 'marketing',
    created_at: '2026-11-11T08:00:00Z',
    last_message_at: '2026-11-11T08:01:00Z',
  },
]

export const demoSaved: SavedQuestion[] = [
  {
    id: 's1',
    text: 'How did revenue change last month?',
    lens: 'business',
    created_at: '2026-11-10T08:00:00Z',
  },
]
