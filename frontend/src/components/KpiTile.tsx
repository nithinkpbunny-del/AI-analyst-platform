import { ArrowDown, ArrowUp } from 'lucide-react'
import { cn } from '../lib/cn'
import { formatINR, formatNumber, formatPct } from '../lib/format'
import { Highlight } from './Highlight'
import { Sparkline } from './Sparkline'

export type KpiFormat = 'inr' | 'number' | 'pct'

export interface KpiTileProps {
  label: string
  value: number
  format?: KpiFormat
  /** Change in percent vs the comparison period, e.g. 12.4 */
  deltaPct?: number
  /** e.g. "last week" */
  comparison?: string
  /** Whether a rise is good news (revenue) or bad news (refunds). */
  goodDirection?: 'up' | 'down'
  trend?: number[]
  /** Put the haldi mark behind this tile's number (only one tile per view). */
  highlight?: boolean
  className?: string
}

function formatValue(value: number, format: KpiFormat): string {
  if (format === 'inr') return formatINR(value)
  if (format === 'pct') return formatPct(value)
  return formatNumber(value)
}

/** Label, big number, delta in colour + arrow + words ("▲ 12.4% vs last week"), sparkline. */
export function KpiTile({
  label,
  value,
  format = 'number',
  deltaPct,
  comparison = 'last period',
  goodDirection = 'up',
  trend,
  highlight = false,
  className,
}: KpiTileProps) {
  const text = formatValue(value, format)
  const rising = deltaPct !== undefined && deltaPct > 0
  const falling = deltaPct !== undefined && deltaPct < 0
  const good = (rising && goodDirection === 'up') || (falling && goodDirection === 'down')
  const bad = (rising && goodDirection === 'down') || (falling && goodDirection === 'up')

  return (
    <article
      aria-label={label}
      className={cn(
        'flex min-w-0 flex-col gap-2 rounded-panel border border-line bg-surface p-4',
        className,
      )}
    >
      <h3 className="font-sans text-small font-medium tracking-normal text-ink-muted">{label}</h3>
      <p className="m-0 font-display text-h2 font-semibold tabular">
        {highlight ? <Highlight>{text}</Highlight> : text}
      </p>
      <div className="flex items-end justify-between gap-3">
        {deltaPct !== undefined ? (
          <p
            className={cn(
              'm-0 inline-flex items-center gap-1 text-small font-medium tabular',
              good && 'text-gain',
              bad && 'text-loss',
              !good && !bad && 'text-ink-muted',
            )}
          >
            {rising && <ArrowUp className="size-4" aria-hidden />}
            {falling && <ArrowDown className="size-4" aria-hidden />}
            <span>
              <span className="sr-only">{rising ? 'Up ' : falling ? 'Down ' : 'No change, '}</span>
              {formatPct(Math.abs(deltaPct))} vs {comparison}
            </span>
          </p>
        ) : (
          <span />
        )}
        {trend ? <Sparkline values={trend} className="shrink-0" /> : null}
      </div>
    </article>
  )
}
