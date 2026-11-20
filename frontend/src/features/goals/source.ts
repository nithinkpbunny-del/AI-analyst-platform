import type { TFunction } from 'i18next'
import type { Provenance } from '../../components/SourceMark'
import type { ResultProvenance } from '../../lib/chatApi'
import { toolLabel } from '../ask/text'

/** Where a computed figure came from, for its SourceMark (BUILD_SPEC 7.7): goals and dashboard tiles (M15). */
export function sourceOf(
  t: TFunction,
  prov: ResultProvenance | undefined,
  computedAt: string,
): Provenance | null {
  if (!prov) return null
  const tables = prov.tables ?? []
  const first = tables[0]
  return {
    connection: first?.connection ?? t('ask.answer.unknownSource'),
    dataset: tables.map((table) => table.name ?? table.table).join(', ') || toolLabel(t, prov.tool),
    query: t('ask.answer.sourceQuery', { tool: toolLabel(t, prov.tool), rows: prov.row_count }),
    fetchedAt: prov.fetched_at ?? computedAt,
    rowCount: first?.rows,
  }
}
