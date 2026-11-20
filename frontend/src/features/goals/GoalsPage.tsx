import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, Pencil, RefreshCw, Target, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { QualityBanner } from '../../components/QualityBanner'
import { SourceMark } from '../../components/SourceMark'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { cn } from '../../lib/cn'
import { formatRelativeTime } from '../../lib/format'
import { formatDay, formatPeriod, formatValue } from '../../lib/goalFormat'
import { goalsApi, type Goal, type GoalList, type GoalStatus } from '../../lib/goalsApi'
import { addStrings } from '../../lib/i18n'
import { useCan } from '../../lib/permissions'
import goalStrings from '../../locales/en/goals'
import { GoalDialog } from './GoalDialog'
import { GOAL_OPTIONS, GOALS } from './keys'
import { sourceOf } from './source'

addStrings(goalStrings)

const POLL_MS = 1500
const OFF_TRACK: GoalStatus[] = ['at_risk', 'behind']

const statusStyle: Record<GoalStatus, string> = {
  on_track: 'bg-gain-soft text-gain',
  achieved: 'bg-gain-soft text-gain',
  at_risk: 'bg-caution-soft text-ink',
  behind: 'bg-loss-soft text-loss',
  missed: 'bg-loss-soft text-loss',
  not_started: 'bg-neel-soft text-neel',
  no_data: 'bg-neel-soft text-ink-muted',
}

function pct(value: number | null | undefined): number {
  if (value === null || value === undefined || !Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, value))
}

function GoalCard({
  goal,
  list,
  canEdit,
  onEdit,
  onRemove,
}: {
  goal: Goal
  list: GoalList
  canEdit: boolean
  onEdit: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const progress = goal.progress
  const status: GoalStatus = progress?.status ?? 'no_data'
  const name = goal.name ?? t('goals.card.metricGoal', { metric: goal.metric_label })
  const source = progress
    ? sourceOf(t, list.provenance[progress.result_id], list.computed_at ?? goal.created_at)
    : null
  const filters = Object.entries(goal.filters)
    .map(([column, value]) => `${column.replace(/_/g, ' ')} = ${value}`)
    .join(', ')
  const reached = pct(progress?.actual_pct_of_target)
  const projected = pct(progress?.projected_pct_of_target)
  const atMost = goal.direction === 'at_most'
  const finished = progress?.method === 'final'
  const mark = source ? <SourceMark index={1} source={source} /> : null
  return (
    <article
      aria-label={t('goals.card.label', { name })}
      className="flex flex-col gap-3 rounded-panel border border-line bg-surface p-4 tablet:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="m-0 font-sans text-body font-semibold tracking-normal">{name}</h2>
          <p className="m-0 text-small text-ink-muted">
            {formatPeriod(goal.period_start, goal.period_end)}
            {filters ? ` · ${t('goals.card.only', { filters })}` : ''}
          </p>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-pill px-3 py-1 text-caption font-semibold',
            statusStyle[status],
          )}
        >
          {t(`goalStatus.${status}`)}
        </span>
      </div>

      {progress && progress.actual !== null ? (
        <>
          <p className="m-0 flex flex-wrap items-baseline gap-x-2">
            <span className="font-display text-h3 font-semibold tabular">
              {formatValue(progress.actual, goal.unit)}
              {mark}
            </span>
            <span className="text-small text-ink-muted tabular">
              {atMost
                ? t('goals.card.ofAtMost', { target: formatValue(goal.target, goal.unit) })
                : t('goals.card.of', { target: formatValue(goal.target, goal.unit) })}
            </span>
          </p>
          <div className="relative">
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(reached)}
              aria-label={t('goals.card.progressLabel', { pct: `${Math.round(reached)}%` })}
              className="h-2.5 overflow-hidden rounded-full bg-neel-soft"
            >
              {/* The projection, lighter, behind the actual. */}
              {!finished ? (
                <div
                  className={cn(
                    'absolute inset-y-0 left-0 rounded-full opacity-35',
                    OFF_TRACK.includes(status) ? 'bg-loss' : 'bg-neel',
                  )}
                  style={{ width: `${projected}%` }}
                />
              ) : null}
              <div
                className={cn(
                  'relative h-full rounded-full',
                  status === 'behind' || status === 'missed' ? 'bg-loss' : 'bg-neel',
                )}
                style={{ width: `${reached}%` }}
              />
            </div>
            {!finished && progress.elapsed_pct > 0 ? (
              <span
                aria-hidden
                title={t('goals.card.timeMarker', { pct: `${Math.round(progress.elapsed_pct)}%` })}
                className="absolute -top-1 h-[18px] w-0.5 bg-ink"
                style={{ left: `calc(${pct(progress.elapsed_pct)}% - 1px)` }}
              />
            ) : null}
          </div>
          <div className="flex flex-col gap-1 text-small tabular">
            {finished ? (
              <p className="m-0">
                {t('goals.card.ended', { value: formatValue(progress.actual, goal.unit) })}
              </p>
            ) : progress.projection !== null ? (
              <p className="m-0">
                {t('goals.card.heading', {
                  value: formatValue(progress.projection, goal.unit),
                  date: formatDay(goal.period_end),
                })}
                {progress.projected_pct_of_target !== null
                  ? ` (${t('goals.card.headingPct', { pct: `${Math.round(progress.projected_pct_of_target)}%` })})`
                  : ''}
              </p>
            ) : null}
            {OFF_TRACK.includes(status) && progress.needed_per_day ? (
              <p className="m-0 font-semibold text-loss">
                {t('goals.card.needed', {
                  amount: formatValue(progress.needed_per_day, goal.unit),
                })}
              </p>
            ) : null}
            <p className="m-0 text-caption text-ink-muted">
              {!finished
                ? `${t('goals.card.day', { elapsed: progress.elapsed_days, total: progress.total_days })} · `
                : ''}
              {progress.method ? t(`goals.card.methods.${progress.method}`) : ''}
              {progress.as_of
                ? ` ${t('goals.card.dataUpTo', { date: formatDay(progress.as_of) })}.`
                : ''}
            </p>
          </div>
        </>
      ) : status === 'not_started' ? (
        <p className="m-0 text-small text-ink-muted">
          {t('goals.card.notStarted', { date: formatDay(goal.period_start) })}{' '}
          {t('goals.card.of', { target: formatValue(goal.target, goal.unit) })}
        </p>
      ) : (
        <p className="m-0 text-small text-ink-muted">
          {t('goals.card.of', { target: formatValue(goal.target, goal.unit) })}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2">
        <span className="text-caption text-ink-muted">
          {goal.created_by ? t('goals.card.setBy', { name: goal.created_by }) : ''}
        </span>
        {canEdit ? (
          <div className="flex gap-1">
            <Button variant="ghost" size="sm" onClick={onEdit}>
              <Pencil aria-hidden strokeWidth={1.5} />
              {t('goals.actions.edit')}
              <span className="sr-only">: {name}</span>
            </Button>
            <Button variant="ghost" size="sm" onClick={onRemove}>
              <Trash2 aria-hidden strokeWidth={1.5} />
              {t('goals.actions.remove')}
              <span className="sr-only">: {name}</span>
            </Button>
          </div>
        ) : null}
      </div>
    </article>
  )
}

/** ``/goals``: targets, progress bars and the projection warning (BUILD_SPEC X7, 11 "Goals"). */
export function GoalsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const canEdit = useCan('goals.edit')
  useEffect(() => {
    document.title = `${t('pages.goals.title')} · ${t('app.name')}`
  }, [t])
  const list = useQuery({
    queryKey: GOALS,
    queryFn: goalsApi.list,
    refetchInterval: (query) => (query.state.data?.refreshing ? POLL_MS : false),
  })
  const options = useQuery({
    queryKey: GOAL_OPTIONS,
    queryFn: goalsApi.options,
    enabled: canEdit,
  })
  const refresh = useMutation({
    mutationFn: (force: boolean) => goalsApi.refresh(force),
    onSuccess: (data) => queryClient.setQueryData(GOALS, data),
  })
  const started = useRef(false)
  useEffect(() => {
    // Opening the screen checks progress when it is older than a few minutes.
    if (!started.current) {
      started.current = true
      refresh.mutate(false)
    }
  }, [refresh])
  const [editing, setEditing] = useState<Goal | 'new' | null>(null)
  const [removing, setRemoving] = useState<Goal | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const remove = useMutation({
    mutationFn: (goal: Goal) => goalsApi.remove(goal.id),
    onSuccess: () => {
      setRemoving(null)
      setNotice(t('goals.form.removed'))
      void queryClient.invalidateQueries({ queryKey: GOALS })
    },
  })
  const saved = (text: string) => {
    setNotice(text)
    refresh.mutate(true)
  }

  const data = list.data
  const offTrack =
    data?.goals.filter((g) => g.progress && OFF_TRACK.includes(g.progress.status)) ?? []
  const busy = refresh.isPending || Boolean(data?.refreshing)
  const noMetrics = canEdit && options.data && options.data.metrics.length === 0

  return (
    <div className="max-w-conversation">
      <PageHeader title={t('pages.goals.title')} intro={t('pages.goals.intro')}>
        {canEdit && options.data && options.data.metrics.length > 0 ? (
          <Button onClick={() => setEditing('new')}>
            <Target aria-hidden strokeWidth={1.5} />
            {t('goals.add')}
          </Button>
        ) : null}
      </PageHeader>

      {notice ? (
        <FormAlert tone="success" className="mb-4">
          {notice}
        </FormAlert>
      ) : null}
      {list.isError ? <FormAlert tone="error">{t('goals.loadFailed')}</FormAlert> : null}
      {list.isPending ? (
        <p className="m-0 text-small text-ink-muted">{t('goals.loading')}</p>
      ) : null}

      {data ? (
        data.goals.length === 0 ? (
          noMetrics ? (
            <EmptyState
              icon={Target}
              message={t('goals.noDictionary')}
              action={
                <Button asChild>
                  <Link to="/dictionary">{t('today.checklist.dictionary')}</Link>
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={Target}
              message={canEdit ? t('pages.goals.empty') : t('goals.emptyViewer')}
              action={
                canEdit && options.data ? (
                  <Button onClick={() => setEditing('new')}>{t('goals.add')}</Button>
                ) : undefined
              }
            />
          )
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p role="status" className="m-0 flex items-center gap-2 text-small text-ink-muted">
                {busy ? (
                  <>
                    <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden />
                    {t('goals.computing')}
                  </>
                ) : data.computed_at ? (
                  t('goals.checked', { when: formatRelativeTime(data.computed_at) })
                ) : (
                  t('goals.notChecked')
                )}
              </p>
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() => refresh.mutate(true)}
              >
                <RefreshCw aria-hidden strokeWidth={1.5} />
                {busy ? t('goals.refreshing') : t('goals.refresh')}
              </Button>
            </div>
            {data.error ? (
              <FormAlert tone="error">
                {t(`goals.errors.${data.error}`, { defaultValue: t('goals.errors.failed') })}
              </FormAlert>
            ) : null}
            {offTrack.length > 0 ? (
              <QualityBanner>{t('goals.warning', { count: offTrack.length })}</QualityBanner>
            ) : null}
            <ul className="m-0 flex list-none flex-col gap-4 p-0">
              {data.goals.map((goal) => (
                <li key={goal.id}>
                  <GoalCard
                    goal={goal}
                    list={data}
                    canEdit={canEdit && Boolean(options.data)}
                    onEdit={() => setEditing(goal)}
                    onRemove={() => setRemoving(goal)}
                  />
                </li>
              ))}
            </ul>
            <p className="m-0 text-caption text-ink-muted">{t('goals.readOnly')}</p>
          </div>
        )
      ) : null}

      {editing && options.data ? (
        <GoalDialog
          key={editing === 'new' ? 'new' : editing.id}
          open
          onOpenChange={(open) => !open && setEditing(null)}
          options={options.data}
          goal={editing === 'new' ? undefined : editing}
          onSaved={saved}
        />
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t('goals.remove.title')}
        description={t('goals.remove.body')}
        confirmLabel={t('goals.remove.confirm')}
        busy={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing)}
      />
    </div>
  )
}
