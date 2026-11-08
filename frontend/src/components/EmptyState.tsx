import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

/** One sentence that says what to do, plus one primary action. */
export function EmptyState({
  icon: Icon,
  message,
  action,
  className,
}: {
  icon?: LucideIcon
  message: string
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-start gap-4 rounded-panel border border-dashed border-line-strong bg-surface p-6',
        className,
      )}
    >
      {Icon ? (
        <span className="flex size-11 items-center justify-center rounded-control bg-neel-soft text-neel">
          <Icon className="size-5" aria-hidden strokeWidth={1.5} />
        </span>
      ) : null}
      <p className="m-0 max-w-prose text-body">{message}</p>
      {action}
    </div>
  )
}
