import { Popover as P } from 'radix-ui'
import type { ComponentPropsWithoutRef } from 'react'
import { cn } from '../../lib/cn'

export const Popover = P.Root
export const PopoverTrigger = P.Trigger

export function PopoverContent({
  className,
  sideOffset = 6,
  ...props
}: ComponentPropsWithoutRef<typeof P.Content>) {
  return (
    <P.Portal>
      <P.Content
        sideOffset={sideOffset}
        collisionPadding={16}
        className={cn(
          'z-50 w-72 max-w-[calc(100vw-32px)] rounded-control border border-line bg-surface p-4 text-small text-ink shadow-overlay',
          'animate-[overlay-fade_150ms_var(--ease-out)]',
          className,
        )}
        {...props}
      />
    </P.Portal>
  )
}
