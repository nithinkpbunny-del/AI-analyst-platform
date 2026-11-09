import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useNavigate } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { authApi, type SetupDoneResponse } from '../../lib/authApi'
import { useAuth, useSession } from '../../lib/authContext'
import { AuthPage } from './AuthLayout'
import { cleanCode, useFinishLogin } from './loginFlow'
import { ProblemAlert, TwoFactorSetup, type SetupStep } from './TwoFactorSetup'

/** Second step of signing in: a code from the app or email, or a recovery code. */
export function TwoFactorPage() {
  const { t } = useTranslation()
  const { state, challenge } = useAuth()
  const finish = useFinishLogin()
  const [recoveryMode, setRecoveryMode] = useState(false)
  const [code, setCode] = useState('')
  const [codeError, setCodeError] = useState<string | undefined>()
  const [problem, setProblem] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (state.status === 'signedIn') return null // just signed in: the navigation is under way
  if (!challenge || challenge.status !== 'two_factor_required') {
    return <Navigate to="/signin" replace />
  }
  const email = challenge.email_hint ?? ''

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const value = cleanCode(code)
    const valid = recoveryMode ? value.length >= 10 : /^\d{6}$/.test(value)
    setProblem(null)
    setNotice(null)
    if (!valid) {
      setCodeError(t('auth.twoFactor.codeRequired'))
      return
    }
    setCodeError(undefined)
    setBusy(true)
    try {
      finish(await authApi.loginVerify(challenge.challenge_token, value), challenge.next)
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      if (error.code === 'invalid_code') setCodeError(error.message)
      else setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  const resend = async () => {
    setProblem(null)
    setNotice(null)
    try {
      await authApi.loginResendCode(challenge.challenge_token)
      setNotice(t('auth.twoFactor.resent'))
    } catch (error) {
      if (error instanceof ApiError) setProblem(error)
      else throw error
    }
  }

  const intro = recoveryMode
    ? t('auth.twoFactor.introRecovery')
    : challenge.method === 'email'
      ? challenge.code_sent
        ? t('auth.twoFactor.introEmail', { email })
        : t('auth.twoFactor.introEmailNotSent', { email })
      : t('auth.twoFactor.introTotp')

  return (
    <AuthPage title={t('auth.twoFactor.title')} intro={intro}>
      {problem ? <ProblemAlert error={problem} /> : null}
      {notice ? <FormAlert tone="success">{notice}</FormAlert> : null}
      <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
        <Field
          key={recoveryMode ? 'recovery' : 'code'}
          label={recoveryMode ? t('auth.twoFactor.recoveryCode') : t('auth.twoFactor.code')}
          name="code"
          inputMode={recoveryMode ? 'text' : 'numeric'}
          autoComplete="one-time-code"
          autoCapitalize="none"
          spellCheck={false}
          maxLength={recoveryMode ? 16 : 9}
          value={code}
          onChange={(event) => setCode(event.target.value)}
          error={codeError}
        />
        <Button type="submit" disabled={busy} className="w-full">
          {t('auth.twoFactor.submit')}
        </Button>
      </form>
      <div className="flex flex-col items-start gap-2">
        {challenge.method === 'email' && !recoveryMode ? (
          <Button variant="link" className="px-0" onClick={() => void resend()}>
            {t('auth.twoFactor.resend')}
          </Button>
        ) : null}
        <Button
          variant="link"
          className="px-0"
          onClick={() => {
            setRecoveryMode((value) => !value)
            setCode('')
            setCodeError(undefined)
          }}
        >
          {recoveryMode ? t('auth.twoFactor.useCode') : t('auth.twoFactor.useRecovery')}
        </Button>
        <Link to="/signin" className="text-small">
          {t('auth.backToSignIn')}
        </Link>
      </div>
    </AuthPage>
  )
}

function setupTitle(step: SetupStep, t: (key: string) => string) {
  return step === 'codes' ? t('setup.recoveryTitle') : t('setup.title')
}

/** An Owner's first sign-in: 2FA must be set up before the sign-in finishes. */
export function LoginSetupPage() {
  const { t } = useTranslation()
  const { state, challenge, acceptLogin } = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState<SetupStep>('choose')
  const [done, setDone] = useState<SetupDoneResponse | null>(null)

  if (state.status === 'signedIn') return null // just signed in: the navigation is under way
  if (!challenge || challenge.status !== 'two_factor_setup_required') {
    return <Navigate to="/signin" replace />
  }
  const token = challenge.challenge_token
  return (
    <AuthPage title={setupTitle(step, t)} intro={step === 'choose' ? t('setup.intro') : undefined}>
      <TwoFactorSetup
        email={done?.user.email ?? challenge.email_hint ?? ''}
        onStep={setStep}
        api={{
          begin: (method) => authApi.loginSetup(token, method),
          resend: () => authApi.loginSetupResend(token),
          confirm: async (code) => {
            const response = await authApi.loginSetupConfirm(token, code)
            setDone(response)
            return response.recovery_codes
          },
        }}
        onFinished={() => {
          // Signed in only now, so the recovery codes stay on screen until they are saved.
          if (!done) return
          const next = challenge.next
          acceptLogin(done, next)
          void navigate(next, { replace: true })
        }}
      />
    </AuthPage>
  )
}

/** Signed in, but an Owner without 2FA (e.g. just made an Owner): set it up before using the app. */
export function RequiredSetupPage() {
  const { t } = useTranslation()
  const session = useSession()
  const { reload } = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState<SetupStep>('choose')
  if (session.two_factor.enabled) return <Navigate to="/today" replace />
  return (
    <AuthPage title={setupTitle(step, t)} intro={step === 'choose' ? t('setup.intro') : undefined}>
      <TwoFactorSetup
        email={session.user.email}
        onStep={setStep}
        api={{
          begin: (method) => authApi.setup(method),
          resend: () => authApi.setupResend(),
          confirm: async (code) => (await authApi.setupConfirm(code)).recovery_codes,
        }}
        onFinished={() => {
          void reload().then(() => navigate('/today', { replace: true }))
        }}
      />
    </AuthPage>
  )
}
