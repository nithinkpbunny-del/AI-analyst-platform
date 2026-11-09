import { Eye, EyeOff } from 'lucide-react'
import { forwardRef, useId, useState, type InputHTMLAttributes, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '../../lib/cn'

const inputClass =
  'block min-h-11 w-full rounded-control border border-line-strong bg-surface px-3 text-body text-ink ' +
  'placeholder:text-ink-muted aria-[invalid=true]:border-loss disabled:opacity-50'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(inputClass, className)} {...props} />
  ),
)
Input.displayName = 'Input'

interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'children'> {
  label: string
  /** Shown under the input until there is an error. */
  hint?: string
  error?: string
  /** Something on the label row's right, e.g. a "Forgot password?" link. */
  aside?: ReactNode
  /** Password fields get a show/hide button. */
  revealable?: boolean
}

/** Label + input + hint/error, wired for screen readers (aria-describedby, aria-invalid). */
export const Field = forwardRef<HTMLInputElement, FieldProps>(
  ({ label, hint, error, aside, revealable, id, type, className, ...props }, ref) => {
    const { t } = useTranslation()
    const autoId = useId()
    const inputId = id ?? autoId
    const noteId = `${inputId}-note`
    const [revealed, setRevealed] = useState(false)
    const note = error ?? hint
    return (
      <div className={cn('flex flex-col gap-1.5', className)}>
        <div className="flex items-baseline justify-between gap-4">
          <label htmlFor={inputId} className="text-small font-medium text-ink">
            {label}
          </label>
          {aside}
        </div>
        <div className="relative">
          <Input
            ref={ref}
            id={inputId}
            type={revealable && revealed ? 'text' : type}
            aria-invalid={error ? true : undefined}
            aria-describedby={note ? noteId : undefined}
            className={revealable ? 'pr-12' : undefined}
            {...props}
          />
          {revealable ? (
            <button
              type="button"
              onClick={() => setRevealed((value) => !value)}
              aria-label={revealed ? t('auth.hidePassword') : t('auth.showPassword')}
              aria-pressed={revealed}
              className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-control text-ink-muted hover:text-ink"
            >
              {revealed ? (
                <EyeOff className="size-5" aria-hidden strokeWidth={1.5} />
              ) : (
                <Eye className="size-5" aria-hidden strokeWidth={1.5} />
              )}
            </button>
          ) : null}
        </div>
        {note ? (
          <p id={noteId} className={cn('m-0 text-small', error ? 'text-loss' : 'text-ink-muted')}>
            {note}
          </p>
        ) : null}
      </div>
    )
  },
)
Field.displayName = 'Field'

/** Native checkbox with a 44 px tap area and an error line. */
export function CheckboxField({
  label,
  error,
  checked,
  onChange,
  name,
}: {
  label: ReactNode
  error?: string
  checked: boolean
  onChange: (checked: boolean) => void
  name: string
}) {
  const id = useId()
  const noteId = `${id}-note`
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-start gap-3">
        <span className="flex size-11 shrink-0 items-center justify-center -m-3">
          <input
            id={id}
            name={name}
            type="checkbox"
            checked={checked}
            onChange={(event) => onChange(event.target.checked)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? noteId : undefined}
            className="size-5 accent-neel"
          />
        </span>
        <label htmlFor={id} className="text-small text-ink">
          {label}
        </label>
      </div>
      {error ? (
        <p id={noteId} className="m-0 text-small text-loss">
          {error}
        </p>
      ) : null}
    </div>
  )
}
