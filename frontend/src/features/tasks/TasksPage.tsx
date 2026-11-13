import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ListChecks } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { ProgressSteps } from '../../components/ProgressSteps'
import { Button } from '../../components/ui/button'
import { SelectField } from '../../components/ui/select'
import { LENSES, type Lens } from '../../lib/chatApi'
import { cn } from '../../lib/cn'
import { formatDateTime } from '../../lib/format'
import { useCan } from '../../lib/permissions'
import { isActive, reportsApi, type Task } from '../../lib/reportsApi'
import { progressText } from '../ask/text'
import { taskReason } from './text'

const TASKS = ['tasks']
const POLL_MS = 2000

const statusTone: Record<Task['status'], string> = {
  queued: 'bg-neel-soft text-ink',
  running: 'bg-neel-soft text-ink',
  done: 'bg-neel-soft text-gain',
  failed: 'bg-neel-soft text-loss',
  cancelled: 'bg-neel-soft text-ink-muted',
}

function TaskRow({ task }: { task: Task }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const cancel = useMutation({
    mutationFn: () => reportsApi.cancelTask(task.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: TASKS }),
  })
  const steps = task.progress.filter((s) => !['done', 'failed', 'cancelled'].includes(s.code))
  return (
    <li className="border-b border-line py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="m-0 font-semibold break-words">{task.title}</p>
          <p className="m-0 mt-1 text-caption text-ink-muted">{formatDateTime(task.created_at)}</p>
        </div>
        <span
          className={cn('rounded-full px-3 py-1 text-caption font-medium', statusTone[task.status])}
        >
          {t(`tasks.statuses.${task.status}`)}
        </span>
      </div>
      {isActive(task) && steps.length > 0 ? (
        <ProgressSteps
          className="mt-3"
          steps={steps.map((step, i) => ({
            id: `${i}-${step.code}`,
            label: progressText(t, step),
            status: i < steps.length - 1 ? 'done' : 'active',
          }))}
        />
      ) : null}
      {task.status === 'failed' ? (
        <p className="m-0 mt-2 text-small text-loss">{taskReason(t, task.error_code)}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {task.status === 'done' && task.report_id ? (
          <Button asChild size="sm">
            <Link to={`/reports/${task.report_id}`}>{t('tasks.openReport')}</Link>
          </Button>
        ) : null}
        {isActive(task) && !task.cancel_requested ? (
          <Button
            variant="secondary"
            size="sm"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
            aria-label={`${t('tasks.cancel')}: ${task.title}`}
          >
            {t('tasks.cancel')}
          </Button>
        ) : null}
        {isActive(task) && task.cancel_requested ? (
          <span className="text-small text-ink-muted">{t('tasks.cancelling')}</span>
        ) : null}
      </div>
    </li>
  )
}

/** ``/tasks``: start a long analysis, follow it, cancel it, open its report (BUILD_SPEC 11 "Long tasks"). */
export function TasksPage() {
  const { t } = useTranslation()
  const canAsk = useCan('ask')
  const queryClient = useQueryClient()
  const [prompt, setPrompt] = useState('')
  const [lens, setLens] = useState<Lens>('business')
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  useEffect(() => {
    document.title = `${t('pages.tasks.title')} · ${t('app.name')}`
  }, [t])
  const tasks = useQuery({
    queryKey: TASKS,
    queryFn: reportsApi.tasks,
    enabled: canAsk,
    refetchInterval: (query) => ((query.state.data?.tasks ?? []).some(isActive) ? POLL_MS : false),
  })
  const submit = useMutation({
    mutationFn: () => reportsApi.submitTask(prompt.trim(), lens),
    onSuccess: () => {
      setPrompt('')
      setNotice({ tone: 'success', text: t('tasks.started') })
      void queryClient.invalidateQueries({ queryKey: TASKS })
    },
    onError: (error) => setNotice({ tone: 'error', text: error.message }),
  })
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setNotice(null)
    if (prompt.trim().length >= 5) submit.mutate()
  }

  if (!canAsk) {
    return (
      <>
        <PageHeader title={t('pages.tasks.title')} intro={t('pages.tasks.intro')} />
        <EmptyState className="max-w-2xl" icon={ListChecks} message={t('tasks.viewerNote')} />
      </>
    )
  }
  const list = tasks.data?.tasks ?? []
  return (
    <div className="max-w-conversation">
      <PageHeader title={t('pages.tasks.title')} intro={t('pages.tasks.intro')} />
      <section
        aria-labelledby="task-form"
        className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
      >
        <h2 id="task-form" className="m-0 text-h4">
          {t('tasks.formTitle')}
        </h2>
        <p className="m-0 mt-2 text-small text-ink-muted">{t('tasks.formIntro')}</p>
        <form noValidate onSubmit={onSubmit} className="mt-4 flex flex-col gap-4">
          {notice ? <FormAlert tone={notice.tone}>{notice.text}</FormAlert> : null}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="task-prompt" className="text-small font-medium">
              {t('tasks.prompt')}
            </label>
            <textarea
              id="task-prompt"
              value={prompt}
              maxLength={4000}
              rows={4}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder={t('tasks.promptPlaceholder')}
              className="min-h-24 rounded-control border border-line-strong bg-surface px-3 py-2.5 text-body text-ink placeholder:text-ink-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-neel"
            />
          </div>
          <SelectField
            label={t('tasks.lens')}
            name="task-lens"
            value={lens}
            onChange={(event) => setLens(event.target.value as Lens)}
          >
            {LENSES.map((option) => (
              <option key={option} value={option}>
                {t(`ask.lens.${option}`)}
              </option>
            ))}
          </SelectField>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={prompt.trim().length < 5 || submit.isPending}>
              {submit.isPending ? t('tasks.submitting') : t('tasks.submit')}
            </Button>
            {tasks.data ? (
              <span className="text-small text-ink-muted">
                {t('tasks.running', { running: tasks.data.running, limit: tasks.data.limit })}
              </span>
            ) : null}
          </div>
        </form>
      </section>
      <section aria-labelledby="task-list" className="mt-8">
        <h2 id="task-list" className="m-0 text-h4">
          {t('tasks.listTitle')}
        </h2>
        {tasks.isSuccess && list.length === 0 ? (
          <p className="m-0 mt-2 text-small text-ink-muted">{t('tasks.empty')}</p>
        ) : (
          <ul className="m-0 mt-2 list-none p-0">
            {list.map((task) => (
              <TaskRow key={task.id} task={task} />
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
