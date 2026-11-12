import { useTranslation } from 'react-i18next'
import type { Provenance } from '../../components/SourceMark'
import type { ChatMessage } from '../../lib/chatApi'
import { toolLabel } from './text'

export const CITATION = /\s*\[(r\d+(?:\s*,\s*r\d+)*)\]/g

export interface Sources {
  /** result id -> its number in this answer (1, 2, 3 …) and where it came from */
  marks: Map<string, { index: number; source: Provenance }>
}

/** Number every cited result once per answer, in the order the answer lists them. */
export function useSources(message: ChatMessage): Sources {
  const { t } = useTranslation()
  const marks = new Map<string, { index: number; source: Provenance }>()
  const cited = message.answer?.cited ?? []
  cited.forEach((rid) => {
    const prov = message.provenance[rid]
    if (!prov) return
    const tables = prov.tables ?? []
    const first = tables[0]
    marks.set(rid, {
      index: marks.size + 1,
      source: {
        connection: first?.connection ?? t('ask.answer.unknownSource'),
        dataset:
          tables.map((table) => table.name ?? table.table).join(', ') || toolLabel(t, prov.tool),
        query: t('ask.answer.sourceQuery', {
          tool: toolLabel(t, prov.tool),
          rows: prov.row_count,
        }),
        fetchedAt: prov.fetched_at ?? message.finished_at ?? message.created_at,
        rowCount: first?.rows,
      },
    })
  })
  return { marks }
}

/** The text without citations (for history titles, copying, screen-reader summaries). */
export function plainText(text: string): string {
  return text.replace(CITATION, '').trim()
}
