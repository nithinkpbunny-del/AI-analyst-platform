import { X } from 'lucide-react'
import { Dialog as D } from 'radix-ui'
import type { ComponentPropsWithoutRef, ReactNode } from 'react'
import { cn } from '../../lib/cn'

export const Sheet = D.Root
export const SheetTrigger = D.Trigger
export const SheetClose = D.Close

/** Bottom sheet on phones (used for "More" and, later, "Show my work"). Slides up in 200 ms. */
export function SheetContent({
  title,
  description,
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<typeof D.Content> & {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-scrim animate-[overlay-fade_150ms_var(--ease-out)]" />
      <D.Content
        className={cn(
          'fixed inset-x-0 bottom-0 z-50 max-h-[85dvh] overflow-y-auto rounded-t-panel border-t border-line bg-surface p-4 pb-8 text-ink shadow-overlay',
          'animate-[sheet-up_200ms_var(--ease-out)]',
          className,
        )}
        {...props}
      >
        <div className="mb-3 flex items-center justify-between gap-4">
          <D.Title className="font-display text-h4">{title}</D.Title>
          <D.Close
            className="inline-flex size-11 items-center justify-center rounded-control text-ink-muted hover:bg-neel-soft"
            aria-label="Close"
          >
            <X className="size-5" aria-hidden />
          </D.Close>
        </div>
        {description ? (
          <D.Description className="mb-3 text-small text-ink-muted">{description}</D.Description>
        ) : (
          <D.Description className="sr-only">{title}</D.Description>
        )}
        {children}
      </D.Content>
    </D.Portal>
  )
}
