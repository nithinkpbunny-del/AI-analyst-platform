import { authedRequest } from './api'
import type { QualityIssue } from './connectionsApi'

export type Lens = 'business' | 'marketing' | 'finance'
export const LENSES: Lens[] = ['business', 'marketing', 'finance']

export type MessageStatus = 'queued' | 'running' | 'done' | 'failed'

/** A step while the answer is being made, e.g. `{code: 'fetching', table: 'orders', connection: 'Shopify'}`. */
export interface ProgressStep {
  code: string
  table?: string
  connection?: string
  tool?: string
  metric?: string
  purpose?: string
}

/** A note written by the system (the web app writes the sentence), e.g. `{code: 'dictionary_not_confirmed'}`. */
export interface AnswerNote {
  code: string
  detail?: string
  metrics?: string[]
}

export interface ChartSeries {
  name: string
  data: (number | string | null)[]
}

export interface ChartSpec {
  chart_id: string
  type: 'line' | 'bar' | 'horizontal_bar' | 'stacked_bar'
  title: string
  x: { name: string; values: (string | number | null)[] }
  series: ChartSeries[]
  result_id: string
  unit: string | null
}

export interface Recommendation {
  text: string
  impact_inr: number | null
  impact_result_id: string | null
  basis_inr: number | null
}

export interface Answer {
  /** 1-2 sentences; figures carry citations like "₹28.0 L [r3]". */
  lead: string
  key_figure: string | null
  evidence: string[]
  why_it_matters: string | null
  recommendation: Recommendation | null
  /** Notes the analyst wrote. */
  data_notes: string[]
  /** Notes written by the system (codes). */
  notes: AnswerNote[]
  next_questions: string[]
  charts: ChartSpec[]
  confidence: 'high' | 'medium' | 'low' | null
  cited: string[]
}

export interface SourceTable {
  table: string
  name?: string
  connection: string | null
  connection_type?: string
  fetched_at: string | null
  rows?: number
}

export interface ResultProvenance {
  result_id: string
  tool: string
  tables: SourceTable[]
  inputs: Record<string, unknown>
  sql: string[]
  row_count: number
  fetched_at: string | null
}

export interface TraceStep {
  id: string
  tool: string
  input: Record<string, unknown>
  ms: number
  result_id?: string
  sql?: string[]
  tables?: string[]
  rows?: number
  error?: string
  chart_id?: string
}

/** A data-quality warning of a table the answer used (the same shape as on the Connections screen). */
export interface QualityWarning extends QualityIssue {
  table: string
  name?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: MessageStatus
  lens: Lens
  created_at: string
  finished_at: string | null
  answer: Answer | null
  progress: ProgressStep[]
  tool_trace: TraceStep[]
  provenance: Record<string, ResultProvenance>
  quality_warnings: QualityWarning[]
  error_code: string | null
  reused: boolean
}

export interface ConversationSummary {
  id: string
  title: string
  lens: Lens
  created_at: string
  last_message_at: string
}

export interface Conversation extends ConversationSummary {
  messages: ChatMessage[]
}

export interface SavedQuestion {
  id: string
  text: string
  lens: Lens
  created_at: string
}

export function isWorking(message: ChatMessage | undefined): boolean {
  return message?.status === 'queued' || message?.status === 'running'
}

export const chatApi = {
  list: () =>
    authedRequest<{ conversations: ConversationSummary[] }>('/conversations').then(
      (r) => r.conversations,
    ),
  get: (id: string) => authedRequest<Conversation>(`/conversations/${id}`),
  start: (question: string, lens: Lens) =>
    authedRequest<Conversation>('/conversations', { method: 'POST', body: { question, lens } }),
  followUp: (id: string, question: string) =>
    authedRequest<Conversation>(`/conversations/${id}/messages`, {
      method: 'POST',
      body: { question },
    }),
  update: (id: string, changes: { title?: string; lens?: Lens }) =>
    authedRequest<ConversationSummary>(`/conversations/${id}`, { method: 'PATCH', body: changes }),
  remove: (id: string) => authedRequest<void>(`/conversations/${id}`, { method: 'DELETE' }),
  saved: () =>
    authedRequest<{ questions: SavedQuestion[] }>('/saved-questions').then((r) => r.questions),
  save: (text: string, lens: Lens) =>
    authedRequest<SavedQuestion>('/saved-questions', { method: 'POST', body: { text, lens } }),
  unsave: (id: string) => authedRequest<void>(`/saved-questions/${id}`, { method: 'DELETE' }),
}
