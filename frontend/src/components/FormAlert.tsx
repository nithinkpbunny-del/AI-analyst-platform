import { CircleAlert, CircleCheck, Info } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

const tones = {
  error: { icon: CircleAlert, box: 'border-loss bg-loss-soft', iconClass: 'text-loss' },
  success: { icon: CircleCheck, box: 'border-gain bg-gain-soft', iconClass: 'text-gain' },
  info: { icon: Info, box: 'border-neel bg-neel-soft', iconClass: 'text-neel' },
} as const

/**
 * Inline message above or below a form. Errors say what happened and how to fix it (BUILD_SPEC 11a.7).
 * Errors are announced immediately (role=alert); success and info politely (role=status).
 */
export function FormAlert({
  tone,
  children,
  action,
  className,
}: {
  tone: keyof typeof tones
  children: ReactNode
  action?: ReactNode
  className?: string
}) {
  const { icon: Icon, box, iconClass } = tones[tone]
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-3 rounded-sheet border-l-4 px-4 py-3 text-small text-ink',
        box,
        className,
      )}
    >
      <Icon className={cn('mt-0.5 size-4 shrink-0', iconClass)} aria-hidden />
      <div className="flex min-w-0 flex-col items-start gap-2">
        <p className="m-0">{children}</p>
        {action}
      </div>
    </div>
  )
}
