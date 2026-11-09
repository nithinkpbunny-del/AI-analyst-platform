import { ChevronDown } from 'lucide-react'
import { forwardRef, useId, type SelectHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

/**
 * Native `<select>` styled with tokens (DECISIONS D37): fully keyboard and screen-reader accessible, and on
 * phones it opens the system picker, which is easier to use than a custom list.
 */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <span className={cn('relative block', className)}>
      <select
        ref={ref}
        className="block min-h-11 w-full cursor-pointer appearance-none rounded-control border border-line-strong bg-surface pr-10 pl-3 text-small text-ink disabled:opacity-50 aria-[invalid=true]:border-loss"
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-ink-muted"
        aria-hidden
      />
    </span>
  ),
)
Select.displayName = 'Select'

/** Label + select, like `Field` for inputs. */
export function SelectField({
  label,
  className,
  id,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  const autoId = useId()
  const selectId = id ?? autoId
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={selectId} className="text-small font-medium text-ink">
        {label}
      </label>
      <Select id={selectId} {...props} />
    </div>
  )
}
