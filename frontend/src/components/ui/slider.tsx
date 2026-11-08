import { Slider as S } from 'radix-ui'
import type { ComponentPropsWithoutRef } from 'react'
import { cn } from '../../lib/cn'

/** Single-thumb slider restyled with tokens; the thumb has a 44 px touch target. */
export function Slider({
  className,
  'aria-label': ariaLabel,
  ...props
}: ComponentPropsWithoutRef<typeof S.Root> & { 'aria-label': string }) {
  return (
    <S.Root
      className={cn('relative flex h-11 w-full touch-none items-center select-none', className)}
      {...props}
    >
      <S.Track className="relative h-1.5 grow rounded-pill bg-line">
        <S.Range className="absolute h-full rounded-pill bg-neel" />
      </S.Track>
      <S.Thumb
        aria-label={ariaLabel}
        className="block size-5 rounded-pill border-2 border-neel bg-surface shadow-overlay"
      />
    </S.Root>
  )
}
