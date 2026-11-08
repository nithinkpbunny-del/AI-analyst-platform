import type { ReactNode } from 'react'
import { cn } from '../lib/cn'
import { formatRelativeTime } from '../lib/format'

export type ConnectionStatus = 'active' | 'needs_reauth' | 'error' | 'disabled'

const statusLabel: Record<ConnectionStatus, string> = {
  active: 'Live',
  needs_reauth: 'Needs sign-in',
  error: 'Error',
  disabled: 'Paused',
}

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
  lastSuccessAt?: Date | string
  /** What happened and how to fix it, shown for errors. */
  message?: string
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
  message,
  actions,
  now,
  className,
}: ConnectionCardProps) {
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
          <h3 className="truncate font-sans text-body font-semibold tracking-normal">{name}</h3>
          <p className="m-0 text-small text-ink-muted">{sourceType}</p>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-pill px-3 py-1 text-caption font-semibold',
            statusStyle[status],
          )}
        >
          {statusLabel[status]}
        </span>
      </div>
      <p className="m-0 text-small text-ink-muted tabular">
        {lastSuccessAt ? `Updated ${formatRelativeTime(lastSuccessAt, now)}` : 'Not fetched yet'}
      </p>
      {message ? <p className="m-0 text-small text-ink">{message}</p> : null}
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </article>
  )
}
