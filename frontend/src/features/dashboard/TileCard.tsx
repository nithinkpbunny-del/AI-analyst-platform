import {
  ArrowDown,
  ArrowUp,
  LoaderCircle,
  MoreHorizontal,
  TriangleAlert,
  Users,
} from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { ChartRenderer } from '../../components/ChartRenderer'
import { Highlight } from '../../components/Highlight'
import { QualityBanner } from '../../components/QualityBanner'
import { SourceMark } from '../../components/SourceMark'
import { Popover, PopoverContent, PopoverTrigger } from '../../components/ui/popover'
import { cn } from '../../lib/cn'
import { formatINR, formatNumber, formatPct, formatRelativeTime } from '../../lib/format'
import type { Tile, TileHeadline } from '../../lib/goalsApi'
import { qualityText } from '../connections/text'
import { sourceOf } from '../goals/source'

function headlineText(headline: TileHeadline): string {
  if (headline.format === 'inr') return formatINR(headline.value)
  if (headline.format === 'pct') return formatPct(headline.value)
  return formatNumber(headline.value)
}

function cell(value: string | number | boolean | null): string {
  if (typeof value === 'number') {
    return Math.abs(value) >= 100 || Number.isInteger(value)
      ? formatNumber(value)
      : String(Math.round(value * 100) / 100)
  }
  if (value === null) return '—'
  return String(value)
}

export interface TileActions {
  onMoveUp?: () => void
  onMoveDown?: () => void
  onSettings?: () => void
  onRemove?: () => void
}

function TileMenu({ title, actions }: { title: string; actions: TileActions }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const items = [
    { label: t('dashboard.menu.moveUp'), run: actions.onMoveUp },
    { label: t('dashboard.menu.moveDown'), run: actions.onMoveDown },
    { label: t('dashboard.menu.settings'), run: actions.onSettings },
    { label: t('dashboard.menu.remove'), run: actions.onRemove },
  ].filter((item) => item.run)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={t('dashboard.menu.open', { title })}
        className="-m-2 inline-flex size-11 shrink-0 items-center justify-center rounded-control text-ink-muted hover:bg-neel-soft hover:text-ink"
      >
        <MoreHorizontal className="size-5" aria-hidden strokeWidth={1.5} />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-1">
        <ul className="m-0 list-none p-0">
          {items.map((item) => (
            <li key={item.label}>
              <button
                type="button"
                className="flex min-h-11 w-full items-center rounded-control px-3 text-left text-small text-ink hover:bg-neel-soft"
                onClick={() => {
                  setOpen(false)
                  item.run?.()
                }}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}

/** One pinned answer, re-run for the person looking (BUILD_SPEC X8, 11a.5 KpiTile). */
export function TileCard({
  tile,
  highlight = false,
  actions,
}: {
  tile: Tile
  highlight?: boolean
  actions?: TileActions
}) {
  const { t } = useTranslation()
  const result = tile.result
  const working = tile.refreshing
  const ok = result?.status === 'ok'
  const headline = ok ? result.headline : null
  const provenance = (ok ? result.provenance : undefined) ?? {}
  const computedAt = result?.computed_at ?? tile.created_at
  const marks = new Map(
    Object.keys(provenance).map((rid, index) => [
      rid,
      { index: index + 1, source: sourceOf(t, provenance[rid], computedAt) },
    ]),
  )
  const markFor = (rid: string | undefined) => {
    const found = rid ? marks.get(rid) : undefined
    return found?.source ? <SourceMark index={found.index} source={found.source} /> : null
  }
  const delta = headline?.delta_pct ?? null
  const rising = delta !== null && delta > 0
  const falling = delta !== null && delta < 0
  const goodDirection = headline?.good_direction ?? 'up'
  const good = (rising && goodDirection === 'up') || (falling && goodDirection === 'down')
  const bad = (rising && goodDirection === 'down') || (falling && goodDirection === 'up')
  const table = ok ? result.table : null

  return (
    <article
      aria-label={t('dashboard.tile.label', { title: tile.title })}
      aria-busy={working || undefined}
      className="flex h-full min-w-0 flex-col gap-3 rounded-panel border border-line bg-surface p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="m-0 font-sans text-body font-semibold tracking-normal">{tile.title}</h3>
          {!tile.mine && tile.owner_name ? (
            <p className="m-0 text-caption text-ink-muted">
              {t('dashboard.tile.sharedBy', { name: tile.owner_name })}
            </p>
          ) : null}
        </div>
        {actions ? <TileMenu title={tile.title} actions={actions} /> : null}
      </div>

      {working && !result ? (
        <p role="status" className="m-0 flex items-center gap-2 text-small text-ink-muted">
          <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden />
          {t('dashboard.tile.computing')}
        </p>
      ) : !result ? (
        <p className="m-0 text-small text-ink-muted">{t('dashboard.tile.notYet')}</p>
      ) : result.status === 'error' ? (
        <p className="m-0 flex items-start gap-2 text-small text-ink">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-caution" aria-hidden />
          {t(`dashboard.tile.errors.${result.error ?? 'internal'}`, {
            defaultValue: t('dashboard.tile.errors.internal'),
          })}
        </p>
      ) : (
        <>
          {headline ? (
            <div className="flex flex-col gap-1">
              {headline.label ? (
                <p className="m-0 text-small text-ink-muted">{headline.label.replace(/_/g, ' ')}</p>
              ) : null}
              <p className="m-0 font-display text-h2 font-semibold tabular">
                {highlight ? (
                  <Highlight>{headlineText(headline)}</Highlight>
                ) : (
                  headlineText(headline)
                )}
                {markFor(headline.result_id)}
              </p>
              {delta !== null ? (
                <p
                  className={cn(
                    'm-0 inline-flex items-center gap-1 text-small font-medium tabular',
                    good && 'text-gain',
                    bad && 'text-loss',
                    !good && !bad && 'text-ink-muted',
                  )}
                >
                  {rising ? <ArrowUp className="size-4" aria-hidden /> : null}
                  {falling ? <ArrowDown className="size-4" aria-hidden /> : null}
                  <span>
                    <span className="sr-only">
                      {rising ? 'Up ' : falling ? 'Down ' : 'No change, '}
                    </span>
                    {formatPct(Math.abs(delta))} vs {t('dashboard.tile.vs')}
                  </span>
                </p>
              ) : null}
            </div>
          ) : null}
          {(result.charts ?? []).map((chart) => (
            <div key={chart.chart_id}>
              <ChartRenderer spec={chart} />
              <p className="m-0 text-caption text-ink-muted">{markFor(chart.result_id)}</p>
            </div>
          ))}
          {table && table.columns.length > 0 ? (
            <div className="overflow-x-auto">
              <table
                aria-label={t('dashboard.tile.tableLabel', { title: tile.title })}
                className="w-full border-collapse text-small tabular"
              >
                <thead>
                  <tr>
                    {table.columns.map((column) => (
                      <th
                        key={column}
                        scope="col"
                        className="border-b border-line px-2 py-1 text-left font-semibold text-ink-muted"
                      >
                        {column.replace(/_/g, ' ')}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((value, j) => (
                        <td key={j} className="border-b border-line px-2 py-1 whitespace-nowrap">
                          {cell(value)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {table.total_rows && table.total_rows > table.rows.length ? (
                <p className="m-0 mt-1 text-caption text-ink-muted">
                  {t('dashboard.tile.moreRows', { count: table.total_rows - table.rows.length })}
                </p>
              ) : null}
            </div>
          ) : null}
          {(result.notes ?? []).map((note) => (
            <p key={note} className="m-0 text-caption text-ink-muted">
              {t(`dashboard.tile.notes.${note}`, { defaultValue: note })}
            </p>
          ))}
          {(result.quality ?? []).slice(0, 1).map((warning, i) => (
            <QualityBanner key={`${warning.table}-${i}`}>
              {warning.name ?? warning.table}: {qualityText(t, warning)}
            </QualityBanner>
          ))}
        </>
      )}

      {result || tile.conversation_id || (tile.mine && tile.shared) ? (
        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2 text-caption text-ink-muted">
          <span className="inline-flex items-center gap-2">
            {working && result ? (
              <LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden />
            ) : null}
            {result
              ? t('dashboard.tile.updated', { when: formatRelativeTime(result.computed_at) })
              : ''}
            {tile.mine && tile.shared ? (
              <span className="inline-flex items-center gap-1">
                <Users className="size-3.5" aria-hidden />
                {t('dashboard.tile.onTeam')}
              </span>
            ) : null}
          </span>
          {tile.conversation_id ? (
            <Link to={`/ask/${tile.conversation_id}`} className="text-neel">
              {t('dashboard.tile.openAnswer')}
              <span className="sr-only">: {tile.title}</span>
            </Link>
          ) : null}
        </div>
      ) : null}
    </article>
  )
}
