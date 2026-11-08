import { useEffect, type ReactNode } from 'react'
import { cn } from '../lib/cn'

let mounted = 0

/**
 * The signature haldi highlighter mark behind the one number that matters (BUILD_SPEC 11a.1, 11a.5).
 * Use it once per view: once per answer, tile or briefing headline. In development a warning is logged when
 * more than one is on screen. The sweep animation is skipped under prefers-reduced-motion.
 */
export function Highlight({
  children,
  sweep = true,
  className,
}: {
  children: ReactNode
  /** Play the 400 ms left-to-right sweep when it appears. */
  sweep?: boolean
  className?: string
}) {
  useEffect(() => {
    mounted += 1
    if (import.meta.env.DEV && mounted > 1) {
      console.warn(
        'Highlight: more than one haldi mark is visible. Use one per view (BUILD_SPEC 11a.5).',
      )
    }
    return () => {
      mounted -= 1
    }
  }, [])

  return (
    <span
      className={cn('relative isolate inline-block whitespace-nowrap', className)}
      data-highlight
    >
      <span
        aria-hidden
        data-testid="haldi-mark"
        className="absolute -inset-x-[0.18em] top-[0.2em] bottom-[0.02em] -z-10 origin-left bg-haldi"
        style={{
          opacity: 'var(--haldi-mark-opacity)',
          transform: 'skewX(-8deg)',
          // Slightly uneven edge, like a real highlighter stroke.
          clipPath:
            'polygon(0% 14%, 7% 5%, 31% 10%, 56% 3%, 81% 9%, 100% 0%, 98% 90%, 72% 100%, 41% 93%, 13% 100%, 1% 88%)',
          animation: sweep ? 'haldi-sweep var(--duration-sweep) var(--ease-out) both' : undefined,
        }}
      />
      {children}
    </span>
  )
}
