import { Copy } from 'lucide-react'
import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { QrCode } from '../../components/QrCode'
import { Button } from '../../components/ui/button'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import type { SetupInfo, TwoFactorMethod } from '../../lib/authApi'
import { cn } from '../../lib/cn'
import { cleanCode } from './loginFlow'
import { RecoveryCodes } from './RecoveryCodes'

export type SetupStep = 'choose' | 'verify' | 'codes'

/** How the wizard talks to the API: during sign-in (challenge token) or signed in (access token). */
export interface SetupApi {
  begin: (method: TwoFactorMethod) => Promise<SetupInfo>
  resend: () => Promise<unknown>
  /** Confirm the code; resolves to the new recovery codes. */
  confirm: (code: string) => Promise<string[]>
}

/** Group a TOTP key in fours so it can be typed: "JBSW Y3DP …". */
function grouped(secret: string): string {
  return secret.replace(/(.{4})(?=.)/g, '$1 ')
}

/** Error box for a failed call, with "Sign in again" when the sign-in itself has expired. */
export function ProblemAlert({ error }: { error: ApiError }) {
  const { t } = useTranslation()
  return (
    <FormAlert
      tone="error"
      action={
        error.code === 'challenge_expired' ? (
          <Link to="/signin" className="font-medium">
            {t('auth.twoFactor.signInAgain')}
          </Link>
        ) : null
      }
    >
      {error.message}
    </FormAlert>
  )
}

function MethodOption({
  value,
  checked,
  onSelect,
  label,
  badge,
  hint,
}: {
  value: TwoFactorMethod
  checked: boolean
  onSelect: (value: TwoFactorMethod) => void
  label: string
  badge?: string
  hint: string
}) {
  const id = useId()
  return (
    <label
      htmlFor={id}
      className={cn(
        'flex cursor-pointer items-start gap-3 rounded-sheet border p-4',
        checked ? 'border-neel bg-neel-soft' : 'border-line-strong bg-surface',
      )}
    >
      <input
        id={id}
        type="radio"
        name="method"
        value={value}
        checked={checked}
        onChange={() => onSelect(value)}
        aria-describedby={`${id}-hint`}
        className="mt-1 size-5 shrink-0 accent-neel"
      />
      <span className="flex flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2 text-body font-medium text-ink">
          {label}
          {badge ? (
            <span className="rounded-pill bg-gain-soft px-2 text-caption text-gain">{badge}</span>
          ) : null}
        </span>
        <span id={`${id}-hint`} className="text-small text-ink-muted">
          {hint}
        </span>
      </span>
    </label>
  )
}

/**
 * Set up (or change) two-step verification: choose a method → prove it works with a code → save recovery codes.
 * `before` renders above the method choice (e.g. a "current code" field for changes made in Settings).
 */
export function TwoFactorSetup({
  api,
  email,
  onStep,
  onFinished,
  finishLabel,
  before,
  canStart = true,
}: {
  api: SetupApi
  email: string
  onStep?: (step: SetupStep) => void
  onFinished: () => void
  finishLabel?: string
  before?: ReactNode
  canStart?: boolean
}) {
  const { t } = useTranslation()
  const [step, setStepState] = useState<SetupStep>('choose')
  const [method, setMethod] = useState<TwoFactorMethod>('totp')
  const [info, setInfo] = useState<SetupInfo | null>(null)
  const [code, setCode] = useState('')
  const [codeError, setCodeError] = useState<string | undefined>()
  const [problem, setProblem] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [recovery, setRecovery] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const setStep = (next: SetupStep) => {
    setStepState(next)
    onStep?.(next)
  }

  const run = async (work: () => Promise<void>) => {
    setProblem(null)
    setNotice(null)
    setBusy(true)
    try {
      await work()
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      if (error.code === 'invalid_code') setCodeError(error.message)
      else setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  const begin = (event: FormEvent) => {
    event.preventDefault()
    if (!canStart) return
    void run(async () => {
      setInfo(await api.begin(method))
      setCode('')
      setCodeError(undefined)
      setStep('verify')
    })
  }

  const confirm = (event: FormEvent) => {
    event.preventDefault()
    const value = cleanCode(code)
    if (!/^\d{6}$/.test(value)) {
      setCodeError(t('auth.twoFactor.codeRequired'))
      return
    }
    setCodeError(undefined)
    void run(async () => {
      setRecovery(await api.confirm(value))
      setStep('codes')
    })
  }

  if (step === 'codes') {
    return (
      <RecoveryCodes codes={recovery} email={email} onDone={onFinished} doneLabel={finishLabel} />
    )
  }

  if (step === 'verify' && info) {
    return (
      <form noValidate onSubmit={confirm} className="flex flex-col gap-5">
        {problem ? <ProblemAlert error={problem} /> : null}
        {notice ? <FormAlert tone="success">{notice}</FormAlert> : null}
        {info.method === 'totp' && info.qr && info.secret ? (
          <>
            <p className="m-0 text-body">{t('setup.scan')}</p>
            <QrCode rows={info.qr} label={t('setup.qrLabel')} />
            <div className="flex flex-col gap-2">
              <p className="m-0 text-small text-ink-muted">{t('setup.cantScan')}</p>
              <div className="flex flex-wrap items-center gap-3">
                <code className="rounded-control bg-neel-soft px-3 py-2 font-sans text-small tracking-wide break-words text-ink tabular-nums">
                  {grouped(info.secret)}
                </code>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    void navigator.clipboard
                      ?.writeText(info.secret ?? '')
                      .then(() => setCopied(true))
                  }}
                >
                  <Copy aria-hidden />
                  {copied ? t('setup.copied') : t('setup.copyKey')}
                </Button>
              </div>
            </div>
            <p className="m-0 text-body">{t('setup.enterAppCode')}</p>
          </>
        ) : (
          <p className="m-0 text-body">
            {t('setup.emailSent', { email: info.email_hint ?? email })}
          </p>
        )}
        <Field
          label={t('auth.twoFactor.code')}
          name="code"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={9}
          value={code}
          onChange={(event) => setCode(event.target.value)}
          error={codeError}
        />
        <Button type="submit" disabled={busy} className="w-full tablet:w-fit">
          {t('setup.confirm')}
        </Button>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {info.method === 'email' ? (
            <Button
              variant="link"
              className="px-0"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.resend()
                  setNotice(t('auth.twoFactor.resent'))
                })
              }
            >
              {t('auth.twoFactor.resend')}
            </Button>
          ) : null}
          <Button variant="link" className="px-0" onClick={() => setStep('choose')}>
            {t('setup.otherMethod')}
          </Button>
        </div>
      </form>
    )
  }

  return (
    <form noValidate onSubmit={begin} className="flex flex-col gap-5">
      {problem ? <ProblemAlert error={problem} /> : null}
      {before}
      <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
        <legend className="mb-3 text-body font-medium text-ink">{t('setup.choose')}</legend>
        <MethodOption
          value="totp"
          checked={method === 'totp'}
          onSelect={setMethod}
          label={t('setup.totpLabel')}
          badge={t('setup.totpRecommended')}
          hint={t('setup.totpHint')}
        />
        <MethodOption
          value="email"
          checked={method === 'email'}
          onSelect={setMethod}
          label={t('setup.emailLabel')}
          hint={t('setup.emailHint')}
        />
      </fieldset>
      {codeError ? <FormAlert tone="error">{codeError}</FormAlert> : null}
      <Button type="submit" disabled={busy || !canStart} className="w-full tablet:w-fit">
        {t('setup.continue')}
      </Button>
    </form>
  )
}
