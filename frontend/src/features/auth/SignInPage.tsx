import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { useAuth } from '../../lib/authContext'
import { authApi } from '../../lib/authApi'
import { AuthPage } from './AuthLayout'
import { GoogleButton } from './GoogleSignIn'
import { looksLikeEmail } from './linkToken'
import { safeNext, useFinishLogin, useGoogleError } from './loginFlow'

interface Problem {
  code: string
  message: string
}

export function SignInPage() {
  const { t } = useTranslation()
  const { state, clearReason } = useAuth()
  const finish = useFinishLogin()
  const location = useLocation()
  const next = safeNext((location.state as { from?: unknown } | null)?.from)
  const googleError = useGoogleError(location.search)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({})
  const [problem, setProblem] = useState<Problem | null>(null)
  const [resent, setResent] = useState(false)
  const [busy, setBusy] = useState(false)
  // Show "you were signed out because…" once, then forget it.
  const [reason] = useState(() => (state.status === 'signedOut' ? state.reason : null))
  const cleared = useRef(false)
  useEffect(() => {
    if (!cleared.current) {
      cleared.current = true
      clearReason()
    }
  }, [clearReason])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const errors: typeof fieldErrors = {}
    if (!looksLikeEmail(email)) errors.email = t('auth.invalidEmail')
    if (!password) errors.password = t('auth.required')
    setFieldErrors(errors)
    setProblem(null)
    setResent(false)
    if (Object.keys(errors).length) return
    setBusy(true)
    try {
      finish(await authApi.login(email.trim(), password), next)
    } catch (error) {
      if (error instanceof ApiError) {
        setProblem({ code: error.code, message: error.message })
        setFieldErrors(error.fields)
      } else throw error
    } finally {
      setBusy(false)
    }
  }

  const resend = async () => {
    await authApi.resendVerification(email.trim()).catch(() => undefined)
    setProblem(null)
    setResent(true)
  }

  return (
    <AuthPage title={t('auth.signIn.title')} intro={t('auth.signIn.intro')}>
      {reason ? <FormAlert tone="info">{reason}</FormAlert> : null}
      {googleError && !problem ? <FormAlert tone="error">{googleError}</FormAlert> : null}
      {resent ? (
        <FormAlert tone="success">{t('auth.signIn.resent', { email: email.trim() })}</FormAlert>
      ) : null}
      {problem ? (
        <FormAlert
          tone="error"
          action={
            problem.code === 'email_not_verified' ? (
              <Button variant="secondary" size="sm" onClick={() => void resend()}>
                {t('auth.signIn.resend')}
              </Button>
            ) : problem.code === 'too_many_attempts' ? (
              <Link to="/forgot-password" className="font-medium">
                {t('auth.signIn.resetInstead')}
              </Link>
            ) : null
          }
        >
          {problem.message}
        </FormAlert>
      ) : null}
      <GoogleButton next={next} />
      <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
        <Field
          label={t('auth.email')}
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={fieldErrors.email}
        />
        <Field
          label={t('auth.password')}
          type="password"
          name="password"
          autoComplete="current-password"
          revealable
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={fieldErrors.password}
          aside={
            <Link to="/forgot-password" className="text-small">
              {t('auth.signIn.forgot')}
            </Link>
          }
        />
        <Button type="submit" disabled={busy} className="w-full">
          {t('auth.signIn.submit')}
        </Button>
      </form>
      <p className="m-0 text-small text-ink-muted">
        {t('auth.signIn.noAccount')} <Link to="/signup">{t('auth.signIn.createAccount')}</Link>
      </p>
    </AuthPage>
  )
}
