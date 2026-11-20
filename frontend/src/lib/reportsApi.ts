import { authedDownload, authedRequest } from './api'
import type { Answer, Lens, ProgressStep, QualityWarning, ResultProvenance } from './chatApi'

// M10: long tasks, reports, exports, sharing, briefings and the Today screen.

const id = encodeURIComponent

// ---------------------------------------------------------------------------------------------- tasks
export type TaskStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface Task {
  id: string
  title: string
  prompt: string
  lens: Lens
  status: TaskStatus
  progress: ProgressStep[]
  error_code: string | null
  report_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  cancel_requested: boolean
}

export interface TaskList {
  tasks: Task[]
  running: number
  limit: number
}

export const isActive = (task: Task) => task.status === 'queued' || task.status === 'running'

// -------------------------------------------------------------------------------------------- reports
export type ExportFormat = 'pdf' | 'xlsx'

export interface KpiFact {
  result_id: string
  span: 'day' | 'week'
  metric: string
  unit: 'INR' | 'count' | null
  current: number | null
  previous: number | null
  change_pct: number | null
  good_direction: 'up' | 'down' | null
}

export type BriefingSectionKey =
  'alerts' | 'unusual' | 'suggested' | 'growth' | 'goals' | 'risks' | 'tasks' | 'health'

export interface BriefingBody {
  kind: 'briefing'
  subject: string
  headline: string
  key_figure: string | null
  data_as_of: string | null
  written_by: 'ai' | 'code'
  sections: { key: BriefingSectionKey; items: string[] }[]
  kpis: KpiFact[]
  notes: { code: string; detail?: string }[]
}

export interface AnswerBody {
  kind: 'answer'
  question: string
  answer: Answer
}

export interface Report {
  id: string
  title: string
  source: 'task' | 'briefing' | 'export'
  body: BriefingBody | AnswerBody
  provenance: Record<string, ResultProvenance>
  quality_warnings: QualityWarning[]
  created_at: string
  mine: boolean
}

export interface ReportSummary {
  id: string
  title: string
  source: Report['source']
  lead: string
  created_at: string
}

// --------------------------------------------------------------------------------------------- shares
export type ShareObject = 'message' | 'report'

export interface ShareEntry {
  id: string
  user_id: string
  name: string | null
  email: string
  created_at: string
}

export interface ShareCandidate {
  user_id: string
  name: string | null
  email: string
  role: 'owner' | 'analyst' | 'viewer'
  can_see: boolean
  shared: boolean
}

export interface ShareList {
  shares: ShareEntry[]
  candidates: ShareCandidate[]
}

export interface SharedSummary {
  id: string
  object_type: ShareObject
  title: string
  lead: string
  shared_by: string | null
  created_at: string
}

export interface SharedItem {
  id: string
  object_type: ShareObject
  title: string
  question: string | null
  answer: Answer | null
  body: BriefingBody | null
  provenance: Record<string, ResultProvenance>
  quality_warnings: QualityWarning[]
  shared_by: string | null
  created_at: string
}

/** What an Export or Share button acts on. */
export type ShareTarget =
  | { kind: 'message'; conversationId: string; messageId: string }
  | { kind: 'report'; reportId: string }
  | { kind: 'shared'; shareId: string }

export function exportPath(target: ShareTarget, format: ExportFormat): string {
  if (target.kind === 'message')
    return `/conversations/${id(target.conversationId)}/messages/${id(target.messageId)}/export?format=${format}`
  if (target.kind === 'report') return `/reports/${id(target.reportId)}/export?format=${format}`
  return `/shared/${id(target.shareId)}/export?format=${format}`
}

// ------------------------------------------------------------------------------------------ briefings
export type Frequency = 'daily' | 'weekly' | 'alerts_only' | 'off'
export const FREQUENCIES: Frequency[] = ['daily', 'weekly', 'alerts_only', 'off']

export interface BriefingSettings {
  enabled: boolean
  local_time: string
  frequency: Frequency
  weekday: number
  language: string
  timezone: string
  default: boolean
  next_at: string | null
}

export interface BriefingRun {
  id: string
  scheduled_for: string
  kind: 'scheduled' | 'test'
  status: 'queued' | 'running' | 'sent' | 'skipped' | 'failed'
  sent_at: string | null
  report_id: string | null
  headline: string | null
  error_code: string | null
}

// ---------------------------------------------------------------------------------------------- today
export interface Checklist {
  company: boolean
  connect: boolean
  dictionary: boolean
  ask: boolean
  done: boolean
}

/** A running goal on Today, as last computed for this person (M15). */
export interface TodayGoal {
  id: string
  label: string
  status: string | null
  actual: number | null
  target: number
  projection: number | null
  projected_pct_of_target: number | null
  unit: string | null
  direction: 'at_least' | 'at_most'
  period_start: string
  period_end: string
  as_of: string | null
}

export interface Today {
  name: string | null
  briefing: Report | null
  suggestions: string[]
  health_check: string
  checklist: Checklist
  tasks: Task[]
  shared: SharedSummary[]
  goals: TodayGoal[]
}

export const reportsApi = {
  tasks: () => authedRequest<TaskList>('/tasks'),
  task: (taskId: string) => authedRequest<Task>(`/tasks/${id(taskId)}`),
  submitTask: (prompt: string, lens: Lens) =>
    authedRequest<Task>('/tasks', { method: 'POST', body: { prompt, lens } }),
  cancelTask: (taskId: string) =>
    authedRequest<Task>(`/tasks/${id(taskId)}/cancel`, { method: 'POST' }),

  reports: (source?: 'task' | 'briefing') =>
    authedRequest<{ reports: ReportSummary[] }>(`/reports${source ? `?source=${source}` : ''}`),
  report: (reportId: string) => authedRequest<Report>(`/reports/${id(reportId)}`),
  download: (target: ShareTarget, format: ExportFormat) =>
    authedDownload(exportPath(target, format)),

  shares: (kind: ShareObject, objectId: string) =>
    authedRequest<ShareList>(`/shares?object_type=${kind}&object_id=${id(objectId)}`),
  share: (kind: ShareObject, objectId: string, userIds: string[]) =>
    authedRequest<ShareList>('/shares', {
      method: 'POST',
      body: { object_type: kind, object_id: objectId, user_ids: userIds },
    }),
  unshare: (shareId: string) => authedRequest<void>(`/shares/${id(shareId)}`, { method: 'DELETE' }),
  shared: () => authedRequest<{ items: SharedSummary[] }>('/shared'),
  sharedItem: (shareId: string) => authedRequest<SharedItem>(`/shared/${id(shareId)}`),

  briefingSettings: () => authedRequest<BriefingSettings>('/briefings/settings'),
  updateBriefingSettings: (
    changes: Partial<
      Pick<BriefingSettings, 'enabled' | 'local_time' | 'frequency' | 'weekday' | 'timezone'>
    >,
  ) => authedRequest<BriefingSettings>('/briefings/settings', { method: 'PUT', body: changes }),
  briefings: () => authedRequest<{ runs: BriefingRun[] }>('/briefings'),
  sendBriefingNow: () => authedRequest<BriefingRun>('/briefings/send-now', { method: 'POST' }),

  today: () => authedRequest<Today>('/today'),
}
