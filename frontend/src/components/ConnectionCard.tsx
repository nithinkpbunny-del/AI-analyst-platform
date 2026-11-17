import '../features/connections/strings'
import { LoaderCircle, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '../lib/cn'
import { formatRelativeTime } from '../lib/format'

export type ConnectionStatus = 'active' | 'needs_reauth' | 'error' | 'disabled'

const statusStyle: Record<ConnectionStatus, string> = {
  active: 'bg-gain-soft text-gain',
  needs_reauth: 'bg-caution-soft text-caution',
  error: 'bg-loss-soft text-loss',
  disabled: 'bg-paper text-ink-muted',
}

export interface ConnectionCardProps {
  name: string
  /** e.g. "Google Sheets" */
  sourceType: string
  status: ConnectionStatus
  lastSuccessAt?: Date | string | null
  /** The source is being read in the background right now. */
  reading?: boolean
  /** e.g. "3 tables · 28,540 rows" */
  summary?: string
  /** Data-quality warnings across the connection's tables. */
  warnings?: number
  /** What happened and how to fix it, shown for errors. */
  message?: string | null
  /** The card's title becomes this (a link to the connection). */
  title?: ReactNode
  actions?: ReactNode
  now?: Date
  className?: string
}

/** A connected data source: initial, status pill, freshness and actions. */
export function ConnectionCard({
  name,
  sourceType,
  status,
  lastSuccessAt,
  reading = false,
  summary,
  warnings = 0,
  message,
  title,
  actions,
  now,
  className,
}: ConnectionCardProps) {
  const { t } = useTranslation()
  return (
    <article
      aria-label={name}
      className={cn(
        'flex flex-col gap-3 rounded-panel border border-line bg-surface p-4',
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex size-11 shrink-0 items-center justify-center rounded-control bg-neel-soft font-display text-h4 font-semibold text-neel"
        >
          {sourceType.charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-sans text-body font-semibold tracking-normal">
            {title ?? name}
          </h3>
          <p className="m-0 text-small text-ink-muted">{sourceType}</p>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-pill px-3 py-1 text-caption font-semibold',
            statusStyle[status],
          )}
        >
          {t(`connections.status.${status}`)}
        </span>
      </div>
      <div className="flex flex-col gap-1 text-small text-ink-muted tabular">
        {reading ? (
          <p role="status" className="m-0 flex items-center gap-2 text-ink">
            <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden />
            {t('connections.reading')}
          </p>
        ) : (
          <p className="m-0">
            {lastSuccessAt
              ? t('connections.updated', { when: formatRelativeTime(lastSuccessAt, now) })
              : t('connections.notFetched')}
          </p>
        )}
        {summary ? <p className="m-0">{summary}</p> : null}
        {warnings > 0 ? (
          <p className="m-0 flex items-center gap-1.5 text-ink">
            <TriangleAlert className="size-4 text-caution" aria-hidden />
            {t('connections.notes', { count: warnings })}
          </p>
        ) : null}
      </div>
      {message ? <p className="m-0 text-small text-ink">{message}</p> : null}
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </article>
  )
}
