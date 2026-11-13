import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Field } from '../../components/ui/input'
import { SelectField } from '../../components/ui/select'
import { ApiError } from '../../lib/api'
import { useAuth, useSession } from '../../lib/authContext'
import { useCan } from '../../lib/permissions'
import { INDUSTRIES, teamApi, type Industry } from '../../lib/teamApi'

const COMMON_ZONES = ['Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Europe/London', 'UTC']

function timeZones(current: string): string[] {
  const all = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []
  return Array.from(new Set([current, ...COMMON_ZONES, ...all]))
}

/** Workspace name and time zone. Owners edit them (`org.manage`); everyone else sees them. */
export function WorkspaceSection() {
  const { t } = useTranslation()
  const { organization } = useSession()
  const { reload } = useAuth()
  const canEdit = useCan('org.manage')
  const [name, setName] = useState(organization.name)
  const [timezone, setTimezone] = useState(organization.timezone)
  const queryClient = useQueryClient()
  // The kind of business is the onboarding's first step (BUILD_SPEC 11a.8) and shapes the metrics.
  const org = useQuery({ queryKey: ['organization'], queryFn: teamApi.organization })
  const [industry, setIndustry] = useState<Industry | ''>('')
  const currentIndustry = org.data?.industry ?? ''
  const chosenIndustry = industry || currentIndustry
  const [error, setError] = useState<string | undefined>()
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const zones = useMemo(() => timeZones(organization.timezone), [organization.timezone])
  const save = useMutation({
    mutationFn: () =>
      teamApi.updateOrganization({
        name: name.trim(),
        timezone,
        ...(chosenIndustry ? { industry: chosenIndustry } : {}),
      }),
    onSuccess: async (data) => {
      queryClient.setQueryData(['organization'], data)
      setIndustry('')
      setNotice({ tone: 'success', text: t('workspace.saved') })
      await reload() // the name shows in the sidebar and the switcher
    },
    onError: (caught) => {
      if (caught instanceof ApiError && caught.fields.name) setError(caught.fields.name)
      else setNotice({ tone: 'error', text: caught.message })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setNotice(null)
    if (!name.trim()) {
      setError(t('auth.required'))
      return
    }
    setError(undefined)
    save.mutate()
  }
  const changed =
    name.trim() !== organization.name ||
    timezone !== organization.timezone ||
    chosenIndustry !== currentIndustry

  return (
    <section
      aria-labelledby="workspace-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="workspace-title" className="text-h4">
        {t('workspace.settingsTitle')}
      </h2>
      <p className="m-0 mt-2 text-small text-ink-muted">{t('workspace.settingsIntro')}</p>
      {canEdit ? (
        <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
          {notice ? <FormAlert tone={notice.tone}>{notice.text}</FormAlert> : null}
          <Field
            label={t('workspace.name')}
            name="workspace-name"
            autoComplete="organization"
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            error={error}
          />
          <SelectField
            label={t('workspace.timezone')}
            name="timezone"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
          >
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone.replaceAll('_', ' ')}
              </option>
            ))}
          </SelectField>
          <SelectField
            label={t('workspace.industry')}
            name="industry"
            value={chosenIndustry}
            onChange={(event) => setIndustry(event.target.value as Industry)}
          >
            {chosenIndustry ? null : <option value="">{t('workspace.industryNone')}</option>}
            {INDUSTRIES.map((option) => (
              <option key={option} value={option}>
                {t(`workspace.industries.${option}`)}
              </option>
            ))}
          </SelectField>
          <p className="-mt-2 mb-0 text-caption text-ink-muted">{t('workspace.industryHint')}</p>
          <Button type="submit" className="w-fit" disabled={!changed || save.isPending}>
            {t('workspace.save')}
          </Button>
        </form>
      ) : (
        <>
          <dl className="m-0 mt-4 grid grid-cols-[auto_1fr] text-small">
            <dt className="flex min-h-11 items-center border-b border-line pr-6 text-ink-muted">
              {t('workspace.name')}
            </dt>
            <dd className="m-0 flex min-h-11 items-center border-b border-line">
              {organization.name}
            </dd>
            <dt className="flex min-h-11 items-center pr-6 text-ink-muted">
              {t('workspace.timezone')}
            </dt>
            <dd className="m-0 flex min-h-11 items-center">
              {organization.timezone.replaceAll('_', ' ')}
            </dd>
          </dl>
          <p className="m-0 mt-2 text-caption text-ink-muted">{t('workspace.readOnly')}</p>
        </>
      )}
    </section>
  )
}
