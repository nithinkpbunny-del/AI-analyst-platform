import { ShieldCheck, ShieldOff } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { authApi } from '../../lib/authApi'
import { useAuth, useSession } from '../../lib/authContext'
import { RecoveryCodes } from '../auth/RecoveryCodes'
import { cleanCode } from '../auth/loginFlow'
import { ProblemAlert, TwoFactorSetup } from '../auth/TwoFactorSetup'

type Mode = 'idle' | 'enable' | 'change' | 'codes' | 'disable'

/** "Code from your current method": the step-up check before changing a 2FA setting. */
function CurrentCodeField({
  value,
  onChange,
  error,
}: {
  value: string
  onChange: (value: string) => void
  error?: string
}) {
  const { t } = useTranslation()
  const session = useSession()
  const [sent, setSent] = useState(false)
  const [problem, setProblem] = useState<ApiError | null>(null)
  const isEmail = session.two_factor.method === 'email'
  return (
    <div className="flex flex-col gap-3">
      {problem ? <ProblemAlert error={problem} /> : null}
      {sent ? <FormAlert tone="success">{t('setup.codeEmailed')}</FormAlert> : null}
      <Field
        label={t('setup.currentCode')}
        name="current_code"
        autoComplete="one-time-code"
        autoCapitalize="none"
        spellCheck={false}
        hint={isEmail ? t('setup.currentCodeHintEmail') : t('setup.currentCodeHintTotp')}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        error={error}
      />
      {isEmail ? (
        <Button
          variant="secondary"
          className="w-fit"
          onClick={() => {
            setProblem(null)
            authApi.sendCode().then(
              () => setSent(true),
              (caught: unknown) => {
                if (caught instanceof ApiError) setProblem(caught)
              },
            )
          }}
        >
          {t('setup.emailMeCode')}
        </Button>
      ) : null}
    </div>
  )
}

/** Recovery codes or turning 2FA off: ask for a current code, then do it. */
function StepUpForm({
  submitLabel,
  onSubmit,
  onCancel,
}: {
  submitLabel: string
  onSubmit: (code: string) => Promise<void>
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [problem, setProblem] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    if (!cleanCode(code)) {
      setError(t('auth.twoFactor.codeRequired'))
      return
    }
    setError(undefined)
    setBusy(true)
    try {
      await onSubmit(cleanCode(code))
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught
      if (caught.code === 'invalid_code') setError(caught.message)
      else setProblem(caught)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-4">
      {problem ? <ProblemAlert error={problem} /> : null}
      <CurrentCodeField value={code} onChange={setCode} error={error} />
      <div className="flex flex-wrap gap-3">
        <Button type="submit" disabled={busy}>
          {submitLabel}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          {t('security.cancel')}
        </Button>
      </div>
    </form>
  )
}

export function SecuritySection() {
  const { t } = useTranslation()
  const session = useSession()
  const { reload } = useAuth()
  const [mode, setMode] = useState<Mode>('idle')
  const [stepUpCode, setStepUpCode] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const { two_factor: tf } = session

  const finish = (message: string | null) => {
    setMode('idle')
    setCodes(null)
    setStepUpCode('')
    setNotice(message)
    void reload()
  }
  const start = (next: Mode) => {
    setNotice(null)
    setMode(next)
  }

  const signedInApi = (code?: string) => ({
    begin: (method: 'totp' | 'email') => authApi.setup(method, code),
    resend: () => authApi.setupResend(),
    confirm: async (value: string) => (await authApi.setupConfirm(value)).recovery_codes,
  })

  const status = tf.enabled
    ? tf.method === 'totp'
      ? t('security.statusOnTotp')
      : t('security.statusOnEmail')
    : t('security.statusOff')
  const StatusIcon = tf.enabled ? ShieldCheck : ShieldOff

  return (
    <section
      aria-labelledby="security-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="security-title" className="text-h4">
        {t('security.title')}
      </h2>
      <p className="m-0 mt-4 flex items-center gap-2 text-body">
        <StatusIcon
          className={tf.enabled ? 'size-5 text-gain' : 'size-5 text-ink-muted'}
          aria-hidden
          strokeWidth={1.5}
        />
        {status}
      </p>
      {tf.enabled ? (
        <p className="m-0 mt-1 text-small text-ink-muted">
          {t('security.remaining', { count: tf.recovery_codes_remaining })}
        </p>
      ) : null}
      {tf.required ? (
        <p className="m-0 mt-1 text-small text-ink-muted">{t('security.required')}</p>
      ) : null}
      {notice ? (
        <FormAlert tone="success" className="mt-4">
          {notice}
        </FormAlert>
      ) : null}

      <div className="mt-4">
        {mode === 'idle' ? (
          <div className="flex flex-wrap gap-3">
            {tf.enabled ? (
              <>
                <Button variant="secondary" onClick={() => start('change')}>
                  {t('security.changeMethod')}
                </Button>
                <Button variant="secondary" onClick={() => start('codes')}>
                  {t('security.newCodes')}
                </Button>
                {!tf.required ? (
                  <Button variant="ghost" onClick={() => start('disable')}>
                    {t('security.turnOff')}
                  </Button>
                ) : null}
              </>
            ) : (
              <Button onClick={() => start('enable')}>{t('security.turnOn')}</Button>
            )}
          </div>
        ) : null}

        {mode === 'enable' ? (
          <TwoFactorSetup
            email={session.user.email}
            api={signedInApi()}
            onFinished={() => finish(t('security.turnedOn'))}
          />
        ) : null}

        {mode === 'change' ? (
          <TwoFactorSetup
            email={session.user.email}
            api={signedInApi(cleanCode(stepUpCode))}
            canStart={Boolean(cleanCode(stepUpCode))}
            before={<CurrentCodeField value={stepUpCode} onChange={setStepUpCode} />}
            onFinished={() => finish(t('security.turnedOn'))}
          />
        ) : null}

        {mode === 'codes' && !codes ? (
          <StepUpForm
            submitLabel={t('security.newCodes')}
            onCancel={() => setMode('idle')}
            onSubmit={async (code) =>
              setCodes((await authApi.newRecoveryCodes(code)).recovery_codes)
            }
          />
        ) : null}
        {mode === 'codes' && codes ? (
          <RecoveryCodes codes={codes} email={session.user.email} onDone={() => finish(null)} />
        ) : null}

        {mode === 'disable' ? (
          <StepUpForm
            submitLabel={t('security.turnOff')}
            onCancel={() => setMode('idle')}
            onSubmit={async (code) => {
              await authApi.disableTwoFactor(code)
              finish(t('security.turnedOff'))
            }}
          />
        ) : null}

        {mode === 'enable' || mode === 'change' ? (
          <Button variant="ghost" className="mt-3" onClick={() => setMode('idle')}>
            {t('security.cancel')}
          </Button>
        ) : null}
      </div>
    </section>
  )
}
