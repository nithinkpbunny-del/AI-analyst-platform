import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { CheckboxField, Field } from '../../components/ui/input'
import { API_BASE_URL, ApiError } from '../../lib/api'
import { authApi } from '../../lib/authApi'
import { AuthPage } from './AuthLayout'
import { ConsentLabel } from './ConsentLabel'
import { useLinkToken } from './linkToken'
import { rememberNext, takeRememberedNext, useFinishLogin } from './loginFlow'

/** Google's "G" mark, as Google's sign-in branding guidelines require (its colours are part of the mark). */
function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden className="size-5">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  )
}

/** "Continue with Google", shown only when the API has Google sign-in switched on. */
export function GoogleButton({ next }: { next: string }) {
  const { t } = useTranslation()
  const providers = useQuery({
    queryKey: ['auth', 'providers'],
    queryFn: authApi.providers,
    retry: false,
    staleTime: Infinity,
  })
  if (!providers.data?.google) return null
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Button asChild variant="secondary" className="w-full">
          <a href={`${API_BASE_URL}/auth/google/start`} onClick={() => rememberNext(next)}>
            <GoogleMark />
            {t('auth.google.button')}
          </a>
        </Button>
        {providers.data.google_stand_in ? (
          <p className="m-0 text-caption text-ink-muted">{t('auth.google.standIn')}</p>
        ) : null}
      </div>
      <div className="flex items-center gap-3 text-small text-ink-muted" aria-hidden>
        <span className="h-px flex-1 bg-line" />
        {t('auth.google.or')}
        <span className="h-px flex-1 bg-line" />
      </div>
    </div>
  )
}

/** `/signin/google#token=…`: Google confirmed an existing account; finish signing in. */
export function GoogleReturnPage() {
  const { t } = useTranslation()
  const token = useLinkToken()
  const finish = useFinishLogin()
  const [problem, setProblem] = useState<string | null>(token ? null : t('auth.google.linkExpired'))
  const started = useRef(false)
  useEffect(() => {
    if (!token || started.current) return
    started.current = true // single-use token: never send it twice (React's double effects)
    authApi
      .googleFinish(token)
      .then((response) => finish(response, takeRememberedNext()))
      .catch((error: unknown) =>
        setProblem(
          error instanceof ApiError ? error.message : t('auth.google.errors.google_failed'),
        ),
      )
  }, [token, finish, t])
  return (
    <AuthPage title={problem ? t('auth.google.failedTitle') : t('auth.signIn.title')}>
      {problem ? (
        <>
          <FormAlert tone="error">{problem}</FormAlert>
          <Button asChild variant="secondary" className="w-fit">
            <Link to="/signin">{t('auth.backToSignIn')}</Link>
          </Button>
        </>
      ) : (
        <p role="status" className="m-0 text-body text-ink-muted">
          {t('auth.google.returning')}
        </p>
      )}
    </AuthPage>
  )
}

/** `/signup/google#token=…`: a new person; name the workspace and accept the terms. */
export function GoogleSignUpPage() {
  const { t } = useTranslation()
  const token = useLinkToken()
  const finish = useFinishLogin()
  const info = useQuery({
    queryKey: ['auth', 'google-signup', token],
    queryFn: () => authApi.googleSignupInfo(token ?? ''),
    enabled: Boolean(token),
    retry: false,
  })
  const [name, setName] = useState<string | null>(null)
  const [company, setCompany] = useState('')
  const [consent, setConsent] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (!token || info.isError) {
    return (
      <AuthPage title={t('auth.google.failedTitle')}>
        <FormAlert tone="error">{t('auth.google.linkExpired')}</FormAlert>
        <Button asChild variant="secondary" className="w-fit">
          <Link to="/signup">{t('auth.backToSignIn')}</Link>
        </Button>
      </AuthPage>
    )
  }
  if (!info.data) {
    return (
      <AuthPage title={t('auth.google.signUpTitle')}>
        <p role="status" className="m-0 text-body text-ink-muted">
          {t('auth.google.returning')}
        </p>
      </AuthPage>
    )
  }
  const shownName = name ?? info.data.name

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const found: Record<string, string> = {}
    if (!shownName.trim()) found.name = t('auth.required')
    if (!company.trim()) found.organization_name = t('auth.required')
    if (!consent) found.accept_terms = t('auth.signUp.consentRequired')
    setErrors(found)
    setProblem(null)
    if (Object.keys(found).length) return
    setBusy(true)
    try {
      const response = await authApi.googleSignup({
        token,
        name: shownName.trim(),
        organization_name: company.trim(),
        accept_terms: true,
      })
      finish(response, '/today')
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      setErrors(error.fields)
      if (!Object.keys(error.fields).length) setProblem(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthPage
      title={t('auth.google.signUpTitle')}
      intro={t('auth.google.signUpIntro', { email: info.data.email })}
    >
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
        <Field
          label={t('auth.name')}
          name="name"
          autoComplete="name"
          value={shownName}
          onChange={(event) => setName(event.target.value)}
          error={errors.name}
        />
        <Field
          label={t('auth.company')}
          name="organization_name"
          autoComplete="organization"
          value={company}
          onChange={(event) => setCompany(event.target.value)}
          error={errors.organization_name}
        />
        <CheckboxField
          name="accept_terms"
          checked={consent}
          onChange={setConsent}
          error={errors.accept_terms}
          label={<ConsentLabel />}
        />
        <Button type="submit" disabled={busy} className="w-full">
          {t('auth.google.signUpSubmit')}
        </Button>
      </form>
    </AuthPage>
  )
}
