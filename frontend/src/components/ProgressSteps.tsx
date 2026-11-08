import { Check, LoaderCircle } from 'lucide-react'
import { cn } from '../lib/cn'

export type StepStatus = 'done' | 'active' | 'pending'
export interface Step {
  id: string
  label: string
  status: StepStatus
}

const statusText: Record<StepStatus, string> = {
  done: 'done',
  active: 'in progress',
  pending: 'waiting',
}

/** Live list of what the agent is doing ("Fetching Shopify orders…"); each finished step gets a tick. */
export function ProgressSteps({ steps, className }: { steps: Step[]; className?: string }) {
  return (
    <ol
      aria-live="polite"
      aria-label="Progress"
      className={cn('m-0 list-none space-y-2 p-0', className)}
    >
      {steps.map((step) => (
        <li
          key={step.id}
          className={cn(
            'flex items-center gap-3 text-small',
            step.status === 'pending' ? 'text-ink-muted' : 'text-ink',
          )}
        >
          <span
            aria-hidden
            className={cn(
              'flex size-5 shrink-0 items-center justify-center rounded-pill border',
              step.status === 'done' && 'border-gain bg-gain text-surface',
              step.status === 'active' && 'border-neel text-neel',
              step.status === 'pending' && 'border-line-strong',
            )}
          >
            {step.status === 'done' && <Check className="size-3.5" strokeWidth={3} />}
            {step.status === 'active' && (
              <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" />
            )}
          </span>
          <span>
            {step.label}
            <span className="sr-only"> ({statusText[step.status]})</span>
          </span>
        </li>
      ))}
    </ol>
  )
}
