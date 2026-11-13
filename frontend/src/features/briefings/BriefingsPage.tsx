import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { CheckboxField, Field } from '../../components/ui/input'
import { SelectField } from '../../components/ui/select'
import { formatDateTime } from '../../lib/format'
import {
  FREQUENCIES,
  reportsApi,
  type BriefingRun,
  type BriefingSettings,
  type Frequency,
} from '../../lib/reportsApi'
import { BRIEFING_SETTINGS, BRIEFINGS } from './keys'

const ZONES = ['Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Europe/London', 'UTC']

function SettingsForm({ settings }: { settings: BriefingSettings }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(settings.enabled)
  const [time, setTime] = useState(settings.local_time)
  const [frequency, setFrequency] = useState<Frequency>(
    settings.frequency === 'off' ? 'daily' : settings.frequency,
  )
  const [weekday, setWeekday] = useState(settings.weekday)
  const [timezone, setTimezone] = useState(settings.timezone)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const zones = useMemo(
    () => Array.from(new Set([settings.timezone, ...ZONES])),
    [settings.timezone],
  )
  const weekdays = t('briefings.weekdays', { returnObjects: true }) as string[]
  const save = useMutation({
    mutationFn: () =>
      reportsApi.updateBriefingSettings({
        enabled,
        local_time: time,
        frequency: enabled ? frequency : 'off',
        weekday,
        timezone,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(BRIEFING_SETTINGS, data)
      setNotice({ tone: 'success', text: t('briefings.saved') })
    },
    onError: (error) => setNotice({ tone: 'error', text: error.message }),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setNotice(null)
    save.mutate()
  }
  return (
    <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
      {notice ? <FormAlert tone={notice.tone}>{notice.text}</FormAlert> : null}
      <CheckboxField
        name="briefing-on"
        label={t('briefings.on')}
        checked={enabled}
        onChange={setEnabled}
      />
      {enabled ? (
        <>
          <SelectField
            label={t('briefings.frequency')}
            name="frequency"
            value={frequency}
            onChange={(event) => setFrequency(event.target.value as Frequency)}
          >
            {FREQUENCIES.filter((f) => f !== 'off').map((option) => (
              <option key={option} value={option}>
                {t(`briefings.frequencies.${option}`)}
              </option>
            ))}
          </SelectField>
          {frequency === 'weekly' ? (
            <SelectField
              label={t('briefings.weekday')}
              name="weekday"
              value={String(weekday)}
              onChange={(event) => setWeekday(Number(event.target.value))}
            >
              {weekdays.map((day, i) => (
                <option key={day} value={i}>
                  {day}
                </option>
              ))}
            </SelectField>
          ) : null}
          <Field
            label={t('briefings.time')}
            name="briefing-time"
            type="time"
            step={300}
            value={time}
            onChange={(event) => setTime(event.target.value)}
          />
          <SelectField
            label={t('briefings.timezone')}
            name="briefing-zone"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
          >
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone.replaceAll('_', ' ')}
              </option>
            ))}
          </SelectField>
          <SelectField label={t('briefings.language')} name="briefing-language" value="en" disabled>
            <option value="en">English</option>
          </SelectField>
          <p className="m-0 text-caption text-ink-muted">{t('briefings.languageNote')}</p>
        </>
      ) : null}
      <Button type="submit" className="w-fit" disabled={save.isPending}>
        {t('briefings.save')}
      </Button>
    </form>
  )
}

function runText(t: ReturnType<typeof useTranslation>['t'], run: BriefingRun): string {
  if (run.status === 'skipped' || run.status === 'failed') {
    const reason = t(`briefings.reasons.${run.error_code ?? 'internal'}`, {
      defaultValue: t('briefings.reasons.internal'),
    })
    return `${t(`briefings.statuses.${run.status}`)}: ${reason}`
  }
  // Citations ([r3]) are source marks in the report; in a one-line list they are left out.
  const headline = (run.headline ?? '').replace(/\s*\[r\d+(?:\s*,\s*r\d+)*\]/g, '')
  return headline || t(`briefings.statuses.${run.status}`)
}

/** ``/briefings``: my briefing settings, "send me one now" and past briefings (BUILD_SPEC 11, R10). */
export function BriefingsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  useEffect(() => {
    document.title = `${t('pages.briefings.title')} · ${t('app.name')}`
  }, [t])
  const settings = useQuery({ queryKey: BRIEFING_SETTINGS, queryFn: reportsApi.briefingSettings })
  const runs = useQuery({ queryKey: BRIEFINGS, queryFn: reportsApi.briefings })
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const sendNow = useMutation({
    mutationFn: reportsApi.sendBriefingNow,
    onSuccess: (run) => {
      setNotice(
        run.status === 'sent'
          ? { tone: 'success', text: t('briefings.sentNow') }
          : { tone: 'error', text: runText(t, run) },
      )
      void queryClient.invalidateQueries({ queryKey: BRIEFINGS })
    },
    onError: (error) => setNotice({ tone: 'error', text: error.message }),
  })
  const data = settings.data
  return (
    <div className="max-w-conversation">
      <PageHeader title={t('pages.briefings.title')} intro={t('pages.briefings.intro')} />
      <section
        aria-labelledby="briefing-settings"
        className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
      >
        <h2 id="briefing-settings" className="m-0 text-h4">
          {t('briefings.settingsTitle')}
        </h2>
        <p className="m-0 mt-2 text-small text-ink-muted">{t('briefings.settingsIntro')}</p>
        {data ? (
          <>
            <p className="m-0 mt-3 text-small">
              {data.next_at
                ? t('briefings.next', { when: formatDateTime(data.next_at, data.timezone) })
                : t('briefings.nextOff')}
            </p>
            {data.default ? (
              <p className="m-0 mt-1 text-caption text-ink-muted">{t('briefings.defaultNote')}</p>
            ) : null}
            <SettingsForm settings={data} />
          </>
        ) : null}
      </section>
      <section className="mt-6 flex flex-col gap-2">
        {notice ? <FormAlert tone={notice.tone}>{notice.text}</FormAlert> : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" disabled={sendNow.isPending} onClick={() => sendNow.mutate()}>
            {sendNow.isPending ? t('today.briefing.sending') : t('briefings.sendNow')}
          </Button>
          <span className="text-caption text-ink-muted">{t('briefings.sendNowHint')}</span>
        </div>
      </section>
      <section aria-labelledby="briefing-archive" className="mt-8">
        <h2 id="briefing-archive" className="m-0 text-h4">
          {t('briefings.archiveTitle')}
        </h2>
        {runs.data && runs.data.runs.length === 0 ? (
          <p className="m-0 mt-2 text-small text-ink-muted">{t('briefings.archiveEmpty')}</p>
        ) : (
          <ul className="m-0 mt-2 list-none p-0">
            {(runs.data?.runs ?? []).map((run) => (
              <li
                key={run.id}
                className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-line py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="m-0 text-small break-words">{runText(t, run)}</p>
                  <p className="m-0 text-caption text-ink-muted">
                    {formatDateTime(run.sent_at ?? run.scheduled_for)}
                    {run.kind === 'test' ? ` · ${t('briefings.test')}` : ''}
                  </p>
                </div>
                {run.report_id ? (
                  <Button asChild variant="ghost" size="sm">
                    <Link to={`/reports/${run.report_id}`}>{t('briefings.open')}</Link>
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
