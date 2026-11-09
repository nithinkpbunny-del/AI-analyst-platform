import { X } from 'lucide-react'
import { Dialog as D } from 'radix-ui'
import type { ComponentPropsWithoutRef, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '../../lib/cn'
import { Button } from './button'

export const Dialog = D.Root
export const DialogTrigger = D.Trigger
export const DialogClose = D.Close

/** Centred dialog that fades and scales in (150 ms). Focus is trapped and returns to the trigger on close. */
export function DialogContent({
  title,
  description,
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<typeof D.Content> & {
  title: string
  description?: ReactNode
  children?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-scrim animate-[overlay-fade_150ms_var(--ease-out)]" />
      <D.Content
        className={cn(
          'fixed top-1/2 left-1/2 z-50 w-[calc(100vw-32px)] max-w-md -translate-x-1/2 -translate-y-1/2',
          'max-h-[85dvh] overflow-y-auto rounded-panel border border-line bg-surface p-6 text-ink shadow-overlay',
          'animate-[dialog-in_150ms_var(--ease-out)]',
          className,
        )}
        {...props}
      >
        <div className="flex items-start justify-between gap-4">
          <D.Title className="font-display text-h4">{title}</D.Title>
          <D.Close
            className="-mt-2 -mr-2 inline-flex size-11 shrink-0 items-center justify-center rounded-control text-ink-muted hover:bg-neel-soft"
            aria-label={t('dialog.close')}
          >
            <X className="size-5" aria-hidden />
          </D.Close>
        </div>
        {description ? (
          <D.Description className="m-0 mt-2 text-body text-ink-muted">{description}</D.Description>
        ) : (
          <D.Description className="sr-only">{title}</D.Description>
        )}
        {children}
      </D.Content>
    </D.Portal>
  )
}

/**
 * "Are you sure?" for actions that are hard to undo. The confirm button repeats the action's own words
 * (BUILD_SPEC 11a.7), e.g. "Remove Ravi".
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  busy = false,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel: string
  onConfirm: () => void
  busy?: boolean
  children?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        {children}
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <DialogClose asChild>
            <Button variant="secondary">{t('dialog.cancel')}</Button>
          </DialogClose>
          <Button variant="danger" disabled={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
