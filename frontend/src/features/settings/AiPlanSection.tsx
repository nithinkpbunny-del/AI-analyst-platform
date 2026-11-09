import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { SelectField } from '../../components/ui/select'
import { aiApi, type AiPlan, type AiProvider, type AiSettings } from '../../lib/aiApi'
import { ApiError } from '../../lib/api'
import { aiErrorText } from './aiErrors'
import { useAuth, useSession } from '../../lib/authContext'
import { cn } from '../../lib/cn'
import { formatDate } from '../../lib/format'
import { useCan } from '../../lib/permissions'

const AI_SETTINGS = ['ai-settings'] as const
const PROVIDERS: AiProvider[] = ['gemini', 'claude']

type Notice = { tone: 'success' | 'error'; text: string } | null

function KeyForm({
  switchAfterSave,
  onSaved,
  onCancel,
}: {
  switchAfterSave: boolean
  onSaved: (settings: AiSettings, switched: boolean) => void
  onCancel?: () => void
}) {
  const { t } = useTranslation()
  const [provider, setProvider] = useState<AiProvider>('gemini')
  const [apiKey, setApiKey] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [problem, setProblem] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: async () => {
      const saved = await aiApi.setKey(provider, apiKey.trim())
      return switchAfterSave ? aiApi.setPlan('byok') : saved
    },
    onSuccess: (settings) => {
      setApiKey('')
      onSaved(settings, switchAfterSave)
    },
    onError: (caught) => {
      if (caught instanceof ApiError && caught.fields.api_key) {
        setError(aiErrorText(t, caught, provider))
      } else setProblem(aiErrorText(t, caught, provider))
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    if (!apiKey.trim()) {
      setError(t('auth.required'))
      return
    }
    setError(undefined)
    save.mutate()
  }
  return (
    <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <SelectField
        label={t('ai.provider')}
        name="ai-provider"
        value={provider}
        onChange={(event) => setProvider(event.target.value as AiProvider)}
      >
        {PROVIDERS.map((value) => (
          <option key={value} value={value}>
            {t(`ai.providers.${value}`)}
          </option>
        ))}
      </SelectField>
      <Field
        label={t('ai.apiKey')}
        name="ai-api-key"
        type="password"
        revealable
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        value={apiKey}
        maxLength={512}
        onChange={(event) => setApiKey(event.target.value)}
        hint={t('ai.apiKeyHint')}
        error={error}
      />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending
            ? t('ai.checking')
            : switchAfterSave
              ? t('ai.saveKeyAndSwitch')
              : t('ai.saveKey')}
        </Button>
        {onCancel ? (
          <Button type="button" variant="secondary" onClick={onCancel}>
            {t('ai.cancel')}
          </Button>
        ) : null}
      </div>
    </form>
  )
}

function PlanChoice({
  settings,
  busy,
  onChoose,
}: {
  settings: AiSettings
  busy: boolean
  onChoose: (plan: AiPlan) => void
}) {
  const { t } = useTranslation()
  const managedProvider = t(`ai.providers.${settings.managed.provider}`)
  const hints: Record<AiPlan, string> = {
    managed: t('ai.planHints.managed', {
      provider: managedProvider,
      limit: settings.managed.monthly_question_limit,
    }),
    byok: settings.own_key
      ? t('ai.planHints.byok', { provider: t(`ai.providers.${settings.own_key.provider}`) })
      : t('ai.planHints.byokNoKey'),
  }
  return (
    <fieldset className="m-0 mt-4 border-0 p-0">
      <legend className="mb-2 text-small font-medium text-ink-muted">{t('ai.planLegend')}</legend>
      <div className="flex flex-col gap-2">
        {(['managed', 'byok'] as const).map((plan) => (
          <label
            key={plan}
            className={cn(
              'flex min-h-11 cursor-pointer items-start gap-3 rounded-control border px-4 py-3',
              settings.plan === plan ? 'border-neel bg-neel-soft' : 'border-line-strong',
            )}
          >
            <input
              type="radio"
              name="ai-plan"
              value={plan}
              checked={settings.plan === plan}
              disabled={busy}
              onChange={() => onChoose(plan)}
              className="mt-1 accent-neel"
            />
            <span className="flex flex-col gap-0.5">
              <span className="text-small font-medium text-ink">{t(`ai.plans.${plan}`)}</span>
              <span className="text-caption text-ink-muted">{hints[plan]}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function ConnectionTest() {
  const { t } = useTranslation()
  const [result, setResult] = useState<Notice>(null)
  const check = useMutation({
    mutationFn: aiApi.check,
    onSuccess: (answer) =>
      setResult({
        tone: 'success',
        text: t('ai.testOk', {
          provider: t(`ai.providers.${answer.provider}`),
          reply: answer.reply || '…',
          model: answer.model,
          tokens: answer.input_tokens + answer.output_tokens,
        }),
      }),
    onError: (caught) => setResult({ tone: 'error', text: aiErrorText(t, caught) }),
  })
  return (
    <div className="mt-6 border-t border-line pt-4">
      <h3 className="m-0 text-body font-semibold">{t('ai.testTitle')}</h3>
      <p className="m-0 mt-1 text-small text-ink-muted">{t('ai.testIntro')}</p>
      <Button
        variant="secondary"
        className="mt-3"
        disabled={check.isPending}
        onClick={() => {
          setResult(null)
          check.mutate()
        }}
      >
        {check.isPending ? t('ai.testing') : t('ai.test')}
      </Button>
      {result ? (
        <FormAlert tone={result.tone} className="mt-3">
          {result.text}
        </FormAlert>
      ) : null}
    </div>
  )
}

function OwnerView() {
  const { t } = useTranslation()
  const { organization } = useSession()
  const { reload } = useAuth()
  const client = useQueryClient()
  const query = useQuery({ queryKey: AI_SETTINGS, queryFn: aiApi.settings })
  const [notice, setNotice] = useState<Notice>(null)
  const [editing, setEditing] = useState(false)
  const [wantsOwnKey, setWantsOwnKey] = useState(false)
  const [removing, setRemoving] = useState(false)

  const applied = async (settings: AiSettings, text: string) => {
    client.setQueryData(AI_SETTINGS, settings)
    void client.invalidateQueries({ queryKey: ['usage'] })
    setNotice({ tone: 'success', text })
    setEditing(false)
    setWantsOwnKey(false)
    await reload() // the plan is part of the session (sidebar, other screens)
  }
  const switchPlan = useMutation({
    mutationFn: aiApi.setPlan,
    onSuccess: (settings) => applied(settings, t(`ai.switched.${settings.plan}`)),
    onError: (caught) => setNotice({ tone: 'error', text: aiErrorText(t, caught) }),
  })
  const remove = useMutation({
    mutationFn: aiApi.removeKey,
    onSuccess: async () => {
      setRemoving(false)
      const settings = await aiApi.settings()
      await applied(settings, t('ai.keyRemoved'))
    },
    onError: (caught) => {
      setRemoving(false)
      setNotice({ tone: 'error', text: aiErrorText(t, caught) })
    },
  })

  if (query.isPending) {
    return (
      <p role="status" className="m-0 mt-4 text-small text-ink-muted">
        {t('ai.loading')}
      </p>
    )
  }
  if (query.isError) {
    return (
      <FormAlert tone="error" className="mt-4">
        {t('ai.loadFailed')}
      </FormAlert>
    )
  }
  const settings = query.data
  const key = settings.own_key
  const choose = (plan: AiPlan) => {
    setNotice(null)
    if (plan === 'byok' && !key) {
      setWantsOwnKey(true)
      return
    }
    setWantsOwnKey(false)
    if (plan !== settings.plan) switchPlan.mutate(plan)
  }
  const showForm = !key || editing

  return (
    <>
      {notice ? (
        <FormAlert tone={notice.tone} className="mt-4">
          {notice.text}
        </FormAlert>
      ) : null}
      {!settings.managed.available && settings.plan === 'managed' ? (
        <FormAlert tone="info" className="mt-4">
          {t('ai.managedUnavailable')}
        </FormAlert>
      ) : null}
      <PlanChoice
        settings={{ ...settings, plan: wantsOwnKey ? 'byok' : settings.plan }}
        busy={switchPlan.isPending}
        onChoose={choose}
      />
      <p className="m-0 mt-2 text-caption text-ink-muted">
        {settings.plan === 'managed' && !wantsOwnKey ? t('ai.managedNotice') : t('ai.byokNotice')}
      </p>

      <div className="mt-6 border-t border-line pt-4">
        <h3 className="m-0 text-body font-semibold">{t('ai.keyTitle')}</h3>
        <p className="m-0 mt-1 text-small text-ink-muted">{t('ai.keyIntro')}</p>
        {key ? (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <KeyRound
                className="mt-0.5 size-5 shrink-0 text-ink-muted"
                aria-hidden
                strokeWidth={1.5}
              />
              <div className="min-w-0">
                <p className="m-0 text-small font-medium tabular-nums">
                  {t('ai.keySummary', {
                    provider: t(`ai.providers.${key.provider}`),
                    last4: `••••${key.last4}`,
                  })}
                </p>
                <p className="m-0 text-caption text-ink-muted">
                  {key.added_by_name
                    ? t('ai.keyAdded', {
                        name: key.added_by_name,
                        date: formatDate(key.added_at, organization.timezone),
                      })
                    : t('ai.keyAddedNoName', {
                        date: formatDate(key.added_at, organization.timezone),
                      })}
                </p>
              </div>
            </div>
            {!editing ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  {t('ai.replaceKey')}
                </Button>
                <Button variant="secondary" onClick={() => setRemoving(true)}>
                  {t('ai.removeKey')}
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="m-0 mt-4 text-small">{t('ai.keyNone')}</p>
        )}
        {showForm ? (
          <KeyForm
            switchAfterSave={wantsOwnKey}
            onCancel={editing ? () => setEditing(false) : undefined}
            onSaved={(saved, switched) =>
              void applied(
                saved,
                switched
                  ? t('ai.switched.byok')
                  : t('ai.keySaved', { last4: saved.own_key?.last4 ?? '' }),
              )
            }
          />
        ) : null}
      </div>

      <ConnectionTest />

      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={t('ai.removeTitle')}
        description={t('ai.removeBody', {
          provider: key ? t(`ai.providers.${key.provider}`) : '',
          last4: key?.last4 ?? '',
        })}
        confirmLabel={t('ai.removeConfirm')}
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </>
  )
}

/** The workspace's AI plan. Owners choose it and manage the key (`org.manage`); everyone else sees it. */
export function AiPlanSection() {
  const { t } = useTranslation()
  const { organization } = useSession()
  const canManage = useCan('org.manage')
  const plan = organization.plan === 'byok' ? 'byok' : 'managed'
  return (
    <section
      aria-labelledby="ai-plan-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="ai-plan-title" className="text-h4">
        {t('ai.title')}
      </h2>
      <p className="m-0 mt-2 text-small text-ink-muted">{t('ai.intro')}</p>
      {canManage ? (
        <OwnerView />
      ) : (
        <p className="m-0 mt-4 text-small">{t('ai.readOnly', { plan: t(`ai.plans.${plan}`) })}</p>
      )}
    </section>
  )
}
