import { TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

/** Inline caution strip for data-quality warnings, e.g. "October data is 60% complete; numbers may be low." */
export function QualityBanner({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      role="note"
      className={cn(
        'flex items-start gap-3 rounded-sheet border-l-4 border-caution bg-caution-soft px-4 py-3 text-small text-ink',
        className,
      )}
    >
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-caution" aria-hidden />
      <p className="m-0">
        <span className="sr-only">Data quality: </span>
        {children}
      </p>
    </div>
  )
}
