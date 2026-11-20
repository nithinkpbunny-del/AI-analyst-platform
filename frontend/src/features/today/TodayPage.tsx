import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Mail, MessageSquareText } from 'lucide-react'
import { lazy, Suspense, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { QualityBanner } from '../../components/QualityBanner'
import { Button } from '../../components/ui/button'
import { useSession } from '../../lib/authContext'
import { cn } from '../../lib/cn'
import { formatDate, formatDateTime } from '../../lib/format'
import { useCan } from '../../lib/permissions'
import { formatValue } from '../../lib/goalFormat'
import { reportsApi, type BriefingBody, type Checklist, type TodayGoal } from '../../lib/reportsApi'
import { teamApi } from '../../lib/teamApi'
import { BriefingView } from '../reports/views'
import '../billing/strings'
import { askLink, TODAY } from './links'

// The kind-of-business picker loads when an Owner opens it (M17), so Today stays light.
const IndustryDialog = lazy(() => import('../templates/IndustryDialog'))

const STEPS: { key: keyof Omit<Checklist, 'done'>; to: (health: string) => string }[] = [
  { key: 'company', to: () => '/settings' },
  { key: 'connect', to: () => '/connections' },
  { key: 'dictionary', to: () => '/dictionary' },
  { key: 'ask', to: (health) => askLink(health, true) },
]

/** The onboarding checklist (BUILD_SPEC 11a.8): four resumable steps to the first insight. */
function OnboardingChecklist({
  checklist,
  health,
  onChooseIndustry,
}: {
  checklist: Checklist
  health: string
  onChooseIndustry?: () => void
}) {
  const { t } = useTranslation()
  const done = STEPS.filter((s) => checklist[s.key]).length
  const next = STEPS.find((s) => !checklist[s.key])
  return (
    <section
      aria-labelledby="checklist-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="checklist-title" className="m-0 text-h4">
          {t('today.checklist.title')}
        </h2>
        <span className="text-small text-ink-muted tabular">
          {t('today.checklist.progress', { done })}
        </span>
      </div>
      <p className="m-0 mt-1 text-small text-ink-muted">{t('today.checklist.intro')}</p>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={4}
        aria-valuenow={done}
        aria-label={t('today.checklist.title')}
        className="mt-3 h-2 overflow-hidden rounded-full bg-neel-soft"
      >
        <div
          className="h-full bg-neel transition-[width]"
          style={{ width: `${(done / 4) * 100}%` }}
        />
      </div>
      <ol className="m-0 mt-4 list-none p-0">
        {STEPS.map((step, i) => {
          const complete = checklist[step.key]
          return (
            <li
              key={step.key}
              className="flex min-h-11 items-center gap-3 border-b border-line py-2 last:border-b-0"
            >
              <span
                className={cn(
                  'flex size-7 shrink-0 items-center justify-center rounded-full text-caption font-semibold',
                  complete ? 'bg-neel text-on-neel' : 'border border-line-strong text-ink-muted',
                )}
                aria-hidden
              >
                {complete ? <Check className="size-4" /> : i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className={cn('m-0 text-body', complete && 'text-ink-muted')}>
                  {t(`today.checklist.${step.key}`)}
                  {complete ? (
                    <span className="sr-only"> ({t('today.checklist.doneLabel')})</span>
                  ) : null}
                </p>
                {!complete ? (
                  <p className="m-0 text-caption text-ink-muted">
                    {t(`today.checklist.${step.key}Hint`)}
                  </p>
                ) : null}
              </div>
              {!complete && step.key === 'company' && onChooseIndustry ? (
                <Button
                  size="sm"
                  variant={step === next ? 'primary' : 'secondary'}
                  onClick={onChooseIndustry}
                >
                  {t('today.checklist.open')}
                  <span className="sr-only">: {t(`today.checklist.${step.key}`)}</span>
                </Button>
              ) : !complete ? (
                <Button asChild size="sm" variant={step === next ? 'primary' : 'secondary'}>
                  <Link to={step.to(health)}>
                    {step.key === 'ask' ? t('today.healthCheck') : t('today.checklist.open')}
                    <span className="sr-only">: {t(`today.checklist.${step.key}`)}</span>
                  </Link>
                </Button>
              ) : null}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

const GOAL_TONE: Record<string, string> = {
  on_track: 'bg-gain-soft text-gain',
  achieved: 'bg-gain-soft text-gain',
  at_risk: 'bg-caution-soft text-ink',
  behind: 'bg-loss-soft text-loss',
  missed: 'bg-loss-soft text-loss',
}

/** Running goals, off-track first (BUILD_SPEC 11a.8 "goal progress"): as last checked, no source is read. */
function TodayGoals({ goals }: { goals: TodayGoal[] }) {
  const { t } = useTranslation()
  return (
    <section aria-labelledby="today-goals">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="today-goals" className="m-0 text-h4">
          {t('today.goals.title')}
        </h2>
        <Link to="/goals" className="text-small text-neel">
          {t('today.goals.all')}
        </Link>
      </div>
      <ul className="m-0 mt-2 list-none p-0">
        {goals.map((goal) => {
          const reached =
            goal.actual !== null && goal.target > 0
              ? Math.max(0, Math.min(100, (goal.actual / goal.target) * 100))
              : 0
          const progress =
            goal.actual === null
              ? t('today.goals.notChecked')
              : t(
                  goal.direction === 'at_most'
                    ? 'today.goals.progressAtMost'
                    : 'today.goals.progress',
                  {
                    actual: formatValue(goal.actual, goal.unit),
                    target: formatValue(goal.target, goal.unit),
                  },
                )
          return (
            <li key={goal.id} className="flex flex-col gap-2 border-b border-line py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-body font-medium">{goal.label}</span>
                {goal.status ? (
                  <span
                    className={cn(
                      'rounded-pill px-3 py-0.5 text-caption font-semibold',
                      GOAL_TONE[goal.status] ?? 'bg-neel-soft text-neel',
                    )}
                  >
                    {t(`goalStatus.${goal.status}`, { defaultValue: goal.status })}
                  </span>
                ) : null}
              </div>
              <div
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(reached)}
                aria-label={goal.label}
                className="h-2 overflow-hidden rounded-full bg-neel-soft"
              >
                <div
                  className={cn(
                    'h-full rounded-full',
                    goal.status === 'behind' || goal.status === 'missed' ? 'bg-loss' : 'bg-neel',
                  )}
                  style={{ width: `${reached}%` }}
                />
              </div>
              <p className="m-0 text-small text-ink-muted tabular">
                {progress}
                {goal.projection !== null && goal.status !== 'achieved'
                  ? `, ${t('today.goals.heading', { value: formatValue(goal.projection, goal.unit) })}`
                  : ''}
              </p>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/** ``/today``: the home screen (BUILD_SPEC 11a.8). The briefing email links here. */
export function TodayPage() {
  const { t } = useTranslation()
  const session = useSession()
  const queryClient = useQueryClient()
  const canAsk = useCan('ask')
  const isOwner = session.role === 'owner'
  useEffect(() => {
    document.title = `${t('pages.today.title')} · ${t('app.name')}`
  }, [t])
  const today = useQuery({ queryKey: TODAY, queryFn: reportsApi.today })
  const org = useQuery({ queryKey: ['organization'], queryFn: teamApi.organization })
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [pickIndustry, setPickIndustry] = useState(false)
  const sendNow = useMutation({
    mutationFn: reportsApi.sendBriefingNow,
    onSuccess: (run) => {
      setNotice(
        run.status === 'sent'
          ? { tone: 'success', text: t('today.briefing.sent') }
          : { tone: 'error', text: t('today.briefing.skipped') },
      )
      void queryClient.invalidateQueries({ queryKey: TODAY })
    },
    onError: (error) => setNotice({ tone: 'error', text: error.message }),
  })

  const data = today.data
  const name = data?.name ?? session.user.name
  const greeting = name
    ? t('today.greeting', { name: name.split(' ')[0] })
    : t('today.greetingNoName')
  const due = org.data?.deletion_due_at
  const briefing = data?.briefing
  const body = briefing?.body.kind === 'briefing' ? (briefing.body as BriefingBody) : null

  return (
    <div className="max-w-conversation">
      <PageHeader title={t('pages.today.title')} intro={`${greeting}. ${t('pages.today.intro')}`} />
      {due ? (
        <QualityBanner className="mb-4">
          {t('today.deletion.banner', { date: formatDate(due) })}{' '}
          {isOwner ? (
            <Link to="/settings" className="text-neel">
              {t('today.deletion.open')}
            </Link>
          ) : null}
        </QualityBanner>
      ) : null}
      {data?.usage_warning ? (
        <QualityBanner className="mb-4">
          {data.usage_warning.level === 'reached'
            ? t('billing.today.reached', {
                limit: data.usage_warning.limit,
                date: formatDate(data.usage_warning.resets_on, 'UTC'),
              })
            : t('billing.today.warning', {
                percent: Math.floor(data.usage_warning.percent),
                used: data.usage_warning.used,
                limit: data.usage_warning.limit,
              })}{' '}
          <Link to="/settings" className="text-neel">
            {t('billing.today.open')}
          </Link>
        </QualityBanner>
      ) : null}
      {today.isError ? <FormAlert tone="error">{t('today.error')}</FormAlert> : null}
      {today.isPending ? (
        <p className="m-0 text-small text-ink-muted">{t('today.loading')}</p>
      ) : null}
      {data ? (
        <div className="flex flex-col gap-8">
          {isOwner && !data.checklist.done ? (
            <OnboardingChecklist
              checklist={data.checklist}
              health={data.health_check}
              onChooseIndustry={() => setPickIndustry(true)}
            />
          ) : null}

          <section aria-labelledby="briefing-title" className="flex flex-col gap-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="briefing-title" className="m-0 text-h4">
                {t('today.briefing.title')}
              </h2>
              {briefing ? (
                <Link to={`/reports/${briefing.id}`} className="text-small text-neel">
                  {t('today.briefing.open')}
                </Link>
              ) : null}
            </div>
            {notice ? <FormAlert tone={notice.tone}>{notice.text}</FormAlert> : null}
            {briefing && body ? (
              <BriefingView
                body={body}
                provenance={briefing.provenance}
                quality={briefing.quality_warnings}
                createdAt={briefing.created_at}
                limit={3}
              />
            ) : (
              <EmptyState
                icon={Mail}
                message={t('today.briefing.none')}
                action={
                  <Button disabled={sendNow.isPending} onClick={() => sendNow.mutate()}>
                    {sendNow.isPending ? t('today.briefing.sending') : t('today.briefing.sendNow')}
                  </Button>
                }
              />
            )}
          </section>

          {data.goals.length > 0 ? <TodayGoals goals={data.goals} /> : null}

          {canAsk ? (
            <section aria-labelledby="suggestions-title">
              <h2 id="suggestions-title" className="m-0 text-h4">
                {t('today.suggestions.title')}
              </h2>
              <ul className="m-0 mt-2 list-none p-0">
                {data.suggestions.map((question) => (
                  <li key={question} className="border-b border-line">
                    <Link
                      to={askLink(question)}
                      className="flex min-h-11 items-center gap-3 py-2 text-ink no-underline hover:text-neel"
                    >
                      <MessageSquareText className="size-4 shrink-0 text-neel" aria-hidden />
                      <span>{question}</span>
                    </Link>
                  </li>
                ))}
              </ul>
              <Button asChild variant="secondary" className="mt-3">
                <Link to={askLink(data.health_check, true)}>{t('today.healthCheck')}</Link>
              </Button>
            </section>
          ) : null}

          {data.tasks.length > 0 ? (
            <section aria-labelledby="today-tasks">
              <div className="flex items-baseline justify-between gap-2">
                <h2 id="today-tasks" className="m-0 text-h4">
                  {t('today.tasks.title')}
                </h2>
                <Link to="/tasks" className="text-small text-neel">
                  {t('today.tasks.all')}
                </Link>
              </div>
              <ul className="m-0 mt-2 list-none p-0">
                {data.tasks.map((task) => (
                  <li
                    key={task.id}
                    className="flex min-h-11 items-center justify-between gap-3 border-b border-line py-2"
                  >
                    <span className="min-w-0 truncate text-body">{task.title}</span>
                    {task.report_id ? (
                      <Link
                        to={`/reports/${task.report_id}`}
                        className="shrink-0 text-small text-neel"
                      >
                        {t('tasks.openReport')}
                      </Link>
                    ) : (
                      <span className="shrink-0 text-small text-ink-muted">
                        {t(`tasks.statuses.${task.status}`)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {data.shared.length > 0 ? (
            <section aria-labelledby="today-shared">
              <div className="flex items-baseline justify-between gap-2">
                <h2 id="today-shared" className="m-0 text-h4">
                  {t('today.shared.title')}
                </h2>
                <Link to="/shared" className="text-small text-neel">
                  {t('today.shared.all')}
                </Link>
              </div>
              <ul className="m-0 mt-2 list-none p-0">
                {data.shared.map((item) => (
                  <li key={item.id} className="border-b border-line">
                    <Link
                      to={`/shared/${item.id}`}
                      className="flex min-h-11 flex-col justify-center py-2 text-ink no-underline hover:text-neel"
                    >
                      <span className="text-body">{item.title}</span>
                      <span className="text-caption text-ink-muted">
                        {item.shared_by ? t('today.shared.by', { name: item.shared_by }) : null}
                        {' · '}
                        {formatDateTime(item.created_at)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
      {pickIndustry ? (
        <Suspense fallback={null}>
          <IndustryDialog
            open
            onOpenChange={setPickIndustry}
            current={org.data?.industry ?? null}
            onSaved={(text) => setNotice({ tone: 'success', text })}
          />
        </Suspense>
      ) : null}
    </div>
  )
}
