import { formatNumber, formatRelativeTime } from '../lib/format'
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover'

export interface Provenance {
  /** e.g. "Shopify" */
  connection: string
  /** In the business's own words, e.g. "orders" */
  dataset: string
  /** Plain-language summary of the query, e.g. "Revenue by region, Jul–Sep 2026" */
  query: string
  fetchedAt: Date | string
  rowCount?: number
}

/**
 * Small superscript number after a figure. Tap or hover opens where it came from: the visible face of the
 * trust features (BUILD_SPEC 7.7, 11a.5).
 */
export function SourceMark({
  index,
  source,
  now,
}: {
  index: number
  source: Provenance
  now?: Date
}) {
  const fetched = formatRelativeTime(source.fetchedAt, now)
  return (
    <Popover>
      <PopoverTrigger
        className="relative -top-[0.5em] ml-0.5 inline-flex min-h-6 min-w-6 items-center justify-center rounded-pill px-1 align-baseline text-caption leading-none font-semibold text-neel hover:bg-neel-soft"
        aria-label={`Source ${index}: ${source.connection} ${source.dataset}, fetched ${fetched}`}
      >
        {index}
      </PopoverTrigger>
      <PopoverContent>
        <p className="font-semibold">
          {source.connection} · {source.dataset}
        </p>
        <p className="mt-1 text-ink-muted">{source.query}</p>
        <p className="mt-2 text-caption text-ink-muted tabular">
          Fetched {fetched}
          {source.rowCount !== undefined ? ` · ${formatNumber(source.rowCount)} rows` : ''}
        </p>
      </PopoverContent>
    </Popover>
  )
}
