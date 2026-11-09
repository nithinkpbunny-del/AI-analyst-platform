import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { authApi } from '../../lib/authApi'
import { AuthPage } from './AuthLayout'
import { looksLikeEmail, useLinkToken } from './linkToken'

function BackToSignIn() {
  const { t } = useTranslation()
  return (
    <p className="m-0 text-small">
      <Link to="/signin">{t('auth.backToSignIn')}</Link>
    </p>
  )
}

/** Email-only form used by "forgot password" and "send a new confirmation link". */
function EmailForm({
  submitLabel,
  onSubmit,
}: {
  submitLabel: string
  onSubmit: (email: string) => Promise<void>
}) {
  const { t } = useTranslation()
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    if (!looksLikeEmail(email)) {
      setError(t('auth.invalidEmail'))
      return
    }
    setError(undefined)
    setBusy(true)
    try {
      await onSubmit(email.trim())
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught
      setError(caught.fields.email)
      if (!caught.fields.email) setProblem(caught.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <Field
        label={t('auth.email')}
        type="email"
        name="email"
        autoComplete="email"
        inputMode="email"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        error={error}
      />
      <Button type="submit" disabled={busy} className="w-full">
        {submitLabel}
      </Button>
    </form>
  )
}

export function CheckEmailPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const email = (location.state as { email?: string } | null)?.email
  const [resent, setResent] = useState(false)
  const resend = async () => {
    if (email) await authApi.resendVerification(email).catch(() => undefined)
    setResent(true)
  }
  return (
    <AuthPage
      title={t('auth.checkEmail.title')}
      intro={email ? t('auth.checkEmail.introWithEmail', { email }) : t('auth.checkEmail.intro')}
    >
      <p className="m-0 text-small text-ink-muted">{t('auth.checkEmail.spam')}</p>
      {resent ? <FormAlert tone="success">{t('auth.checkEmail.resent')}</FormAlert> : null}
      {email ? (
        <Button variant="secondary" className="w-fit" onClick={() => void resend()}>
          {t('auth.checkEmail.resend')}
        </Button>
      ) : null}
      <BackToSignIn />
    </AuthPage>
  )
}

export function VerifyEmailPage() {
  const { t } = useTranslation()
  const token = useLinkToken()
  const [result, setResult] = useState<'checking' | 'done' | 'failed'>(
    token ? 'checking' : 'failed',
  )
  const [resent, setResent] = useState(false)
  const started = useRef(false)
  useEffect(() => {
    // The link is single-use: make sure React's double effects in development do not spend it twice.
    if (!token || started.current) return
    started.current = true
    authApi
      .verifyEmail(token)
      .then(() => setResult('done'))
      .catch(() => setResult('failed'))
  }, [token])

  return (
    <AuthPage title={t('auth.verify.title')}>
      {result === 'checking' ? (
        <p role="status" className="m-0 text-body text-ink-muted">
          {t('auth.verify.checking')}
        </p>
      ) : null}
      {result === 'done' ? (
        <>
          <FormAlert tone="success">{t('auth.verify.done')}</FormAlert>
          <Button asChild className="w-fit">
            <Link to="/signin">{t('auth.verify.signIn')}</Link>
          </Button>
        </>
      ) : null}
      {result === 'failed' ? (
        <>
          <FormAlert tone="error">{t('auth.verify.failed')}</FormAlert>
          {resent ? (
            <FormAlert tone="success">{t('auth.verify.resent')}</FormAlert>
          ) : (
            <>
              <p className="m-0 text-body">{t('auth.verify.askAgain')}</p>
              <EmailForm
                submitLabel={t('auth.verify.resend')}
                onSubmit={async (email) => {
                  await authApi.resendVerification(email)
                  setResent(true)
                }}
              />
            </>
          )}
          <BackToSignIn />
        </>
      ) : null}
    </AuthPage>
  )
}

export function ForgotPasswordPage() {
  const { t } = useTranslation()
  const [message, setMessage] = useState<string | null>(null)
  return (
    <AuthPage title={t('auth.forgot.title')} intro={t('auth.forgot.intro')}>
      {message ? (
        <FormAlert tone="success">{message}</FormAlert>
      ) : (
        <EmailForm
          submitLabel={t('auth.forgot.submit')}
          onSubmit={async (email) => {
            const response = await authApi.forgotPassword(email)
            setMessage(response.message)
          }}
        />
      )}
      <BackToSignIn />
    </AuthPage>
  )
}

export function ResetPasswordPage() {
  const { t } = useTranslation()
  const token = useLinkToken()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [outcome, setOutcome] = useState<'form' | 'done' | 'invalid'>(token ? 'form' : 'invalid')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    if (password.length < 10) {
      setError(t('auth.tooShort'))
      return
    }
    setError(undefined)
    setBusy(true)
    try {
      await authApi.resetPassword(token ?? '', password)
      setOutcome('done')
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught
      if (caught.code === 'invalid_link') setOutcome('invalid')
      else if (caught.fields.password) setError(caught.fields.password)
      else setProblem(caught.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthPage
      title={t('auth.reset.title')}
      intro={outcome === 'form' ? t('auth.reset.intro') : undefined}
    >
      {outcome === 'form' ? (
        <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
          {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
          <Field
            label={t('auth.newPassword')}
            type="password"
            name="password"
            autoComplete="new-password"
            revealable
            hint={t('auth.passwordHint')}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            error={error}
          />
          <Button type="submit" disabled={busy} className="w-full">
            {t('auth.reset.submit')}
          </Button>
        </form>
      ) : null}
      {outcome === 'done' ? (
        <>
          <FormAlert tone="success">{t('auth.reset.done')}</FormAlert>
          <Button asChild className="w-fit">
            <Link to="/signin">{t('auth.reset.signIn')}</Link>
          </Button>
        </>
      ) : null}
      {outcome === 'invalid' ? (
        <>
          <FormAlert tone="error">{t('auth.reset.invalid')}</FormAlert>
          <Button asChild variant="secondary" className="w-fit">
            <Link to="/forgot-password">{t('auth.reset.askAgain')}</Link>
          </Button>
        </>
      ) : null}
    </AuthPage>
  )
}
