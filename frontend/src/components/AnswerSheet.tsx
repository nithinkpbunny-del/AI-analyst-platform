import { Bookmark, FileDown, ListTree, Pin, Share2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '../lib/cn'
import { Button } from './ui/button'
import { TriangleAlert } from 'lucide-react'

export interface AnswerSheetProps {
  /** The 1–2 sentence answer. Wrap the key number in <Highlight>. */
  lead: ReactNode
  evidence: { id: string; content: ReactNode }[]
  /** ChartRenderer output (arrives with the Ask screen in M8). */
  chart?: ReactNode
  whyItMatters?: ReactNode
  recommendation?: ReactNode
  dataNotes?: string[]
  nextQuestions?: string[]
  onNextQuestion?: (question: string) => void
  onSave?: () => void
  onPin?: () => void
  onExport?: () => void
  onShare?: () => void
  onShowWork?: () => void
  className?: string
}

/** The answer, laid out like a ledger page (BUILD_SPEC 7.4 answer format, 11a.5). */
export function AnswerSheet({
  lead,
  evidence,
  chart,
  whyItMatters,
  recommendation,
  dataNotes = [],
  nextQuestions = [],
  onNextQuestion,
  onSave,
  onPin,
  onExport,
  onShare,
  onShowWork,
  className,
}: AnswerSheetProps) {
  const { t } = useTranslation()
  const actions = [
    { label: t('ask.actions.save'), icon: Bookmark, onClick: onSave },
    { label: t('ask.actions.pin'), icon: Pin, onClick: onPin },
    { label: t('ask.actions.export'), icon: FileDown, onClick: onExport },
    { label: t('ask.actions.share'), icon: Share2, onClick: onShare },
    { label: t('ask.work.open'), icon: ListTree, onClick: onShowWork },
  ].filter((a) => a.onClick)

  return (
    <article
      aria-label={t('ask.answer.label')}
      className={cn(
        'max-w-conversation rounded-sheet border border-line bg-surface p-4 tablet:p-6',
        className,
      )}
    >
      <p className="m-0 animate-[lead-fade_var(--duration-fade)_var(--ease-out)_both] font-display text-h4 leading-snug font-semibold text-balance tablet:text-h3">
        {lead}
      </p>

      {evidence.length > 0 && (
        <section aria-label={t('ask.answer.evidence')} className="mt-4">
          <h3 className="sr-only">{t('ask.answer.evidence')}</h3>
          <ul className="m-0 list-none p-0">
            {evidence.map((row) => (
              <li
                key={row.id}
                className="flex min-h-11 items-center border-t border-line py-2 text-body tabular last:border-b"
              >
                {row.content}
              </li>
            ))}
          </ul>
        </section>
      )}

      {chart ? <div className="mt-4">{chart}</div> : null}

      {whyItMatters ? (
        <section className="mt-4">
          <h3 className="font-sans text-small font-semibold tracking-normal">
            {t('ask.answer.whyItMatters')}
          </h3>
          <div className="mt-1 text-body">{whyItMatters}</div>
        </section>
      ) : null}

      {recommendation ? (
        <section className="mt-4">
          <h3 className="font-sans text-small font-semibold tracking-normal">
            {t('ask.answer.recommendation')}
          </h3>
          <div className="mt-1 text-body">{recommendation}</div>
        </section>
      ) : null}

      {dataNotes.length > 0 && (
        <section aria-label={t('ask.answer.dataNotes')} className="mt-4">
          <h3 className="font-sans text-small font-semibold tracking-normal text-caution">
            {t('ask.answer.dataNotes')}
          </h3>
          <ul className="m-0 mt-1 list-none space-y-1 p-0">
            {dataNotes.map((note) => (
              <li key={note} className="flex items-start gap-2 text-small text-ink">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-caution" aria-hidden />
                {note}
              </li>
            ))}
          </ul>
        </section>
      )}

      {nextQuestions.length > 0 && (
        <section aria-label={t('ask.answer.nextQuestions')} className="mt-4 flex flex-wrap gap-2">
          {nextQuestions.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => onNextQuestion?.(q)}
              className="min-h-11 rounded-pill border border-line-strong px-4 text-left text-small text-ink hover:bg-neel-soft"
            >
              {q}
            </button>
          ))}
        </section>
      )}

      {actions.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1 border-t border-line pt-3">
          {actions.map(({ label, icon: Icon, onClick }) => (
            <Button key={label} variant="ghost" size="sm" onClick={onClick}>
              <Icon aria-hidden strokeWidth={1.5} />
              {label}
            </Button>
          ))}
        </div>
      )}
    </article>
  )
}
