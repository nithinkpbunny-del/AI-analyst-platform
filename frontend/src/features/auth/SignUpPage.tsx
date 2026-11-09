import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { CheckboxField, Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { authApi } from '../../lib/authApi'
import { AuthPage } from './AuthLayout'
import { ConsentLabel } from './ConsentLabel'
import { GoogleButton } from './GoogleSignIn'
import { looksLikeEmail } from './linkToken'

type FieldName = 'name' | 'email' | 'organization_name' | 'password' | 'accept_terms'

export function SignUpPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [values, setValues] = useState({ name: '', email: '', organization_name: '', password: '' })
  const [accepted, setAccepted] = useState(false)
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const set = (name: keyof typeof values) => (event: { target: { value: string } }) =>
    setValues((current) => ({ ...current, [name]: event.target.value }))

  const validate = () => {
    const found: typeof errors = {}
    if (!values.name.trim()) found.name = t('auth.required')
    if (!looksLikeEmail(values.email)) found.email = t('auth.invalidEmail')
    if (!values.organization_name.trim()) found.organization_name = t('auth.required')
    if (values.password.length < 10) found.password = t('auth.tooShort')
    if (!accepted) found.accept_terms = t('auth.signUp.consentRequired')
    return found
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const found = validate()
    setErrors(found)
    setProblem(null)
    if (Object.keys(found).length) return
    setBusy(true)
    try {
      await authApi.signup({
        name: values.name.trim(),
        email: values.email.trim(),
        organization_name: values.organization_name.trim(),
        password: values.password,
        accept_terms: accepted,
      })
      void navigate('/check-email', { state: { email: values.email.trim() } })
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      // Field problems show next to the field; anything else (e.g. too many requests) above the form.
      setErrors(error.fields as typeof errors)
      setProblem(Object.keys(error.fields).length ? null : error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthPage title={t('auth.signUp.title')} intro={t('auth.signUp.intro')}>
      <GoogleButton next="/today" />
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
        <Field
          label={t('auth.name')}
          name="name"
          autoComplete="name"
          value={values.name}
          onChange={set('name')}
          error={errors.name}
        />
        <Field
          label={t('auth.workEmail')}
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          value={values.email}
          onChange={set('email')}
          error={errors.email}
        />
        <Field
          label={t('auth.company')}
          name="organization_name"
          autoComplete="organization"
          value={values.organization_name}
          onChange={set('organization_name')}
          error={errors.organization_name}
        />
        <Field
          label={t('auth.password')}
          type="password"
          name="password"
          autoComplete="new-password"
          revealable
          hint={t('auth.passwordHint')}
          value={values.password}
          onChange={set('password')}
          error={errors.password}
        />
        <CheckboxField
          name="accept_terms"
          checked={accepted}
          onChange={setAccepted}
          error={errors.accept_terms}
          label={<ConsentLabel />}
        />
        <Button type="submit" disabled={busy} className="w-full">
          {t('auth.signUp.submit')}
        </Button>
      </form>
      <p className="m-0 text-small text-ink-muted">
        {t('auth.signUp.haveAccount')} <Link to="/signin">{t('auth.signUp.signIn')}</Link>
      </p>
    </AuthPage>
  )
}
