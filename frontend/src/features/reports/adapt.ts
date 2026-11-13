import type { Answer, ChatMessage, QualityWarning, ResultProvenance } from '../../lib/chatApi'

/** A stored answer (a long task's report, an item shared with me) as the message the Ask screen draws. */
export function answerMessage({
  id,
  answer,
  provenance,
  quality,
  createdAt,
}: {
  id: string
  answer: Answer
  provenance: Record<string, ResultProvenance>
  quality: QualityWarning[]
  createdAt: string
}): ChatMessage {
  return {
    id,
    role: 'assistant',
    content: answer.lead,
    status: 'done',
    lens: 'business',
    created_at: createdAt,
    finished_at: createdAt,
    answer: {
      ...answer,
      charts: answer.charts ?? [],
      notes: answer.notes ?? [],
      data_notes: answer.data_notes ?? [],
      next_questions: answer.next_questions ?? [],
      evidence: answer.evidence ?? [],
      cited: answer.cited ?? [],
    },
    progress: [],
    tool_trace: [],
    provenance,
    quality_warnings: quality,
    error_code: null,
    reused: false,
  }
}
