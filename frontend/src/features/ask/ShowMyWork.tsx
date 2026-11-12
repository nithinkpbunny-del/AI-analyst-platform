import { useTranslation } from 'react-i18next'
import { Sheet, SheetContent } from '../../components/ui/sheet'
import type { ChatMessage, SourceTable } from '../../lib/chatApi'
import { formatRelativeTime } from '../../lib/format'
import { codeKey, toolLabel } from './text'

/**
 * "Show my work" (BUILD_SPEC 11): each step with its query, the sources with their fetch times. A drawer at the
 * side on wider screens, a full-height sheet on phones.
 */
export function ShowMyWork({
  message,
  open,
  onOpenChange,
}: {
  message: ChatMessage | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const sources = new Map<string, SourceTable>()
  for (const prov of Object.values(message?.provenance ?? {})) {
    for (const table of prov.tables ?? []) {
      if (table.table && !sources.has(table.table)) sources.set(table.table, table)
    }
  }
  const trace = message?.tool_trace ?? []
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        title={t('ask.work.title')}
        description={t('ask.work.intro')}
        className="tablet:inset-y-0 tablet:right-0 tablet:left-auto tablet:max-h-none tablet:w-[440px] tablet:rounded-none tablet:border-t-0 tablet:border-l"
      >
        <section aria-labelledby="work-steps">
          <h3 id="work-steps" className="m-0 text-small font-semibold">
            {t('ask.work.steps')}
          </h3>
          {trace.length === 0 ? (
            <p className="text-small text-ink-muted">{t('ask.work.none')}</p>
          ) : (
            <ol className="m-0 mt-2 list-none space-y-3 p-0">
              {trace.map((step, i) => (
                <li key={`${step.id}-${i}`} className="border-t border-line pt-3">
                  <p className="m-0 flex flex-wrap items-baseline justify-between gap-2 text-small">
                    <span className="font-medium">
                      {i + 1}. {toolLabel(t, step.tool)}
                    </span>
                    <span className="text-caption text-ink-muted tabular">
                      {step.rows !== undefined
                        ? `${t('ask.work.rows', { count: step.rows })} · `
                        : ''}
                      {t('ask.work.took', { ms: step.ms })}
                    </span>
                  </p>
                  {step.error ? (
                    <p className="m-0 mt-1 text-caption text-caution">
                      {t('ask.work.error', { code: codeKey(step.error) })}
                    </p>
                  ) : null}
                  {(step.sql ?? []).slice(-2).map((sql, j) => (
                    <pre
                      key={j}
                      aria-label={t('ask.work.query')}
                      className="m-0 mt-2 max-h-40 overflow-auto rounded-sheet bg-paper p-2 text-caption leading-relaxed whitespace-pre-wrap text-ink"
                    >
                      {sql}
                    </pre>
                  ))}
                </li>
              ))}
            </ol>
          )}
        </section>
        {sources.size > 0 ? (
          <section aria-labelledby="work-sources" className="mt-6">
            <h3 id="work-sources" className="m-0 text-small font-semibold">
              {t('ask.work.sources')}
            </h3>
            <ul className="m-0 mt-2 list-none p-0">
              {[...sources.values()].map((table) => (
                <li key={table.table} className="border-t border-line py-2 text-small">
                  <span className="font-medium">{table.connection}</span> ·{' '}
                  {table.name ?? table.table}
                  <span className="block text-caption text-ink-muted tabular">
                    {table.fetched_at
                      ? t('ask.work.fetched', { when: formatRelativeTime(table.fetched_at) })
                      : t('ask.answer.sourceNoFetch')}
                    {table.rows !== undefined
                      ? ` · ${t('ask.work.rows', { count: table.rows })}`
                      : ''}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
