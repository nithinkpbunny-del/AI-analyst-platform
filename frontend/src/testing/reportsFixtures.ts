import type {
  BriefingRun,
  BriefingSettings,
  Report,
  ShareList,
  Task,
  TaskList,
  Today,
} from '../lib/reportsApi'
import type { OrganizationSettings } from '../lib/teamApi'
import { demoAnswer } from './askFixtures'

/** M10 fixtures from the UrbanCart demo company: the briefing on the morning after the App outage (finding 5). */
const ordersProvenance = {
  result_id: 'r3',
  tool: 'detect_anomalies',
  tables: [
    {
      table: 'orders',
      name: 'Orders',
      connection: 'Shopify',
      fetched_at: '2026-08-16T00:25:00Z',
      rows: 28540,
    },
  ],
  inputs: { metric: 'revenue' },
  sql: [],
  row_count: 3,
  fetched_at: '2026-08-16T00:25:00Z',
}

export const demoBriefing: Report = {
  id: 'rep-briefing',
  title: 'Morning briefing, 16 Aug 2026',
  source: 'briefing',
  body: {
    kind: 'briefing',
    subject: 'Revenue fell on 15 Aug',
    headline: 'Revenue on 15 Aug: ₹70,633 [r3], -54.3% [r3] against a usual ₹1.5 L [r3].',
    key_figure: '-54.3%',
    data_as_of: '2026-08-15',
    written_by: 'ai',
    sections: [
      { key: 'alerts', items: ['App orders fell on 15 Aug: check the app [r3].'] },
      {
        key: 'unusual',
        items: ['Revenue on 12 Aug: ₹1.8 L [r3], +79.3% [r3] against a usual ₹1.0 L [r3].'],
      },
      {
        key: 'growth',
        items: [
          'West × Beauty: revenue rose 3 months in a row, 73% [r4] of it from repeat customers.',
        ],
      },
      { key: 'suggested', items: ['Back what is working in West × Beauty.'] },
    ],
    kpis: [
      {
        result_id: 'r2',
        span: 'week',
        metric: 'revenue',
        unit: 'INR',
        current: 838684,
        previous: 1068148,
        change_pct: -21.5,
        good_direction: 'up',
      },
      {
        result_id: 'r2',
        span: 'week',
        metric: 'orders',
        unit: 'count',
        current: 388,
        previous: 399,
        change_pct: -2.8,
        good_direction: 'up',
      },
    ],
    notes: [],
  },
  provenance: {
    r3: ordersProvenance,
    r4: { ...ordersProvenance, result_id: 'r4', tool: 'growth_signals' },
  },
  quality_warnings: [],
  created_at: '2026-08-16T00:30:00Z',
  mine: true,
}

export const demoTaskReport: Report = {
  id: 'rep-task',
  title: 'Review Q3 2026 by region',
  source: 'task',
  body: {
    kind: 'answer',
    question: 'Review Q3 2026 by region and tell me what to fix.',
    answer: demoAnswer.answer!,
  },
  provenance: demoAnswer.provenance,
  quality_warnings: [],
  created_at: '2026-11-12T11:00:00Z',
  mine: true,
}

export function demoTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task1',
    title: 'Review Q3 2026 by region',
    prompt: 'Review Q3 2026 by region and tell me what to fix.',
    lens: 'business',
    status: 'done',
    progress: [{ code: 'reading_question' }, { code: 'done' }],
    error_code: null,
    report_id: 'rep-task',
    created_at: '2026-11-12T10:30:00Z',
    started_at: '2026-11-12T10:30:01Z',
    finished_at: '2026-11-12T10:41:00Z',
    cancel_requested: false,
    ...overrides,
  }
}

export const demoTasks: TaskList = {
  tasks: [
    demoTask({
      id: 'task2',
      title: 'Full marketing review for 2026',
      status: 'running',
      report_id: null,
      finished_at: null,
      progress: [
        { code: 'reading_question' },
        { code: 'fetching', table: 'Marketing', connection: 'Google Sheets' },
      ],
    }),
    demoTask(),
    demoTask({
      id: 'task3',
      title: 'Margin by product',
      status: 'failed',
      error_code: 'limit:steps',
      report_id: null,
    }),
  ],
  running: 1,
  limit: 2,
}

export const demoToday: Today = {
  name: 'Asha Rao',
  briefing: demoBriefing,
  suggestions: [
    'Why is West × Beauty growing, and can we repeat it elsewhere?',
    'What happened to revenue on 2026-08-15?',
    'How did revenue change last month, and why?',
  ],
  health_check:
    'Give me a health check of the business for the last complete month: the main numbers, what changed and why, and what is working well.',
  checklist: { company: true, connect: true, dictionary: false, ask: false, done: false },
  tasks: [demoTask()],
  shared: [
    {
      id: 'share1',
      object_type: 'message',
      title: 'Why did South revenue fall in Q3?',
      lead: 'South revenue fell 17.7% in Q3.',
      shared_by: 'Ravi',
      created_at: '2026-11-12T12:00:00Z',
    },
  ],
  goals: [
    {
      id: 'goal-nov',
      label: 'November sales',
      status: 'behind',
      actual: 1840000,
      target: 5000000,
      projection: 3480000,
      projected_pct_of_target: 69.6,
      unit: 'INR',
      direction: 'at_least',
      period_start: '2026-11-01',
      period_end: '2026-11-30',
      as_of: '2026-11-15',
    },
  ],
}

export const demoBriefingSettings: BriefingSettings = {
  enabled: true,
  local_time: '06:00',
  frequency: 'daily',
  weekday: 0,
  language: 'en',
  timezone: 'Asia/Kolkata',
  default: true,
  next_at: '2026-11-14T00:30:00Z',
}

export const demoRuns: BriefingRun[] = [
  {
    id: 'run1',
    scheduled_for: '2026-11-13T00:30:00Z',
    kind: 'scheduled',
    status: 'sent',
    sent_at: '2026-11-13T00:31:00Z',
    report_id: 'rep-briefing',
    headline: 'Revenue on 15 Aug: ₹70,633 [r3], -54.3% [r3] against a usual ₹1.5 L [r3].',
    error_code: null,
  },
  {
    id: 'run2',
    scheduled_for: '2026-11-12T00:30:00Z',
    kind: 'scheduled',
    status: 'skipped',
    sent_at: null,
    report_id: null,
    headline: null,
    error_code: 'nothing_to_report',
  },
]

export const demoShares: ShareList = {
  shares: [
    {
      id: 'share9',
      user_id: 'u-meera',
      name: 'Meera Shah',
      email: 'meera@urbancart.in',
      created_at: '2026-11-12T12:00:00Z',
    },
  ],
  candidates: [
    {
      user_id: 'u-ravi',
      name: 'Ravi',
      email: 'ravi@urbancart.in',
      role: 'analyst',
      can_see: true,
      shared: false,
    },
    {
      user_id: 'u-lata',
      name: 'Lata',
      email: 'lata@urbancart.in',
      role: 'viewer',
      can_see: false,
      shared: false,
    },
    {
      user_id: 'u-meera',
      name: 'Meera Shah',
      email: 'meera@urbancart.in',
      role: 'owner',
      can_see: true,
      shared: true,
    },
  ],
}

export function demoOrg(overrides: Partial<OrganizationSettings> = {}): OrganizationSettings {
  return {
    id: 'org-a',
    name: 'UrbanCart',
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    plan: 'managed',
    industry: 'd2c',
    deletion_requested_at: null,
    deletion_due_at: null,
    ...overrides,
  }
}
