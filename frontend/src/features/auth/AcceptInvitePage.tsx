import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { CheckboxField, Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { useAuth } from '../../lib/authContext'
import { invitationApi, type InvitationPreview } from '../../lib/teamApi'
import { AuthPage } from './AuthLayout'
import { ConsentLabel } from './ConsentLabel'
import { GoogleButton } from './GoogleSignIn'
import { useLinkToken } from './linkToken'
import { useFinishLogin } from './loginFlow'

type Errors = Partial<Record<'name' | 'password' | 'accept_terms', string>>

function errorsFrom(error: ApiError): Errors {
  return error.fields as Errors
}

/** A new person: name, password and the terms. The link already proves the email address. */
function NewAccountForm({ token, invitation }: { token: string; invitation: InvitationPreview }) {
  const { t } = useTranslation()
  const finish = useFinishLogin()
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [accepted, setAccepted] = useState(false)
  const [errors, setErrors] = useState<Errors>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const found: Errors = {}
    if (!name.trim()) found.name = t('auth.required')
    if (password.length < 10) found.password = t('auth.tooShort')
    if (!accepted) found.accept_terms = t('auth.signUp.consentRequired')
    setErrors(found)
    setProblem(null)
    if (Object.keys(found).length) return
    setBusy(true)
    try {
      finish(
        await invitationApi.acceptNew({
          token,
          name: name.trim(),
          password,
          accept_terms: accepted,
        }),
        '/today',
      )
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      setErrors(errorsFrom(error))
      setProblem(Object.keys(error.fields).length ? null : error.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
      <p className="m-0 text-body">{t('invite.newIntro', { email: invitation.email })}</p>
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <Field
        label={t('auth.name')}
        name="name"
        autoComplete="name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        error={errors.name}
      />
      <Field
        label={t('auth.password')}
        type="password"
        name="password"
        autoComplete="new-password"
        revealable
        hint={t('auth.passwordHint')}
        value={password}
        onChange={(event) => setPassword(event.target.value)}
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
        {t('invite.create')}
      </Button>
    </form>
  )
}

/** An existing account, signed out: its password (then the usual second step, if any). */
function ExistingAccountForm({
  token,
  invitation,
}: {
  token: string
  invitation: InvitationPreview
}) {
  const { t } = useTranslation()
  const finish = useFinishLogin()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    if (!password) {
      setError(t('auth.required'))
      return
    }
    setError(undefined)
    setBusy(true)
    try {
      finish(await invitationApi.acceptWithPassword(token, password), '/today')
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught
      setProblem(caught.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-5">
      <p className="m-0 text-body">{t('invite.existingIntro', { email: invitation.email })}</p>
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <Field
        label={t('auth.password')}
        type="password"
        name="password"
        autoComplete="current-password"
        revealable
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        error={error}
        aside={<Link to="/forgot-password">{t('auth.signIn.forgot')}</Link>}
      />
      <Button type="submit" disabled={busy} className="w-full">
        {t('invite.join')}
      </Button>
    </form>
  )
}

/** Signed in already: one click if it is the invited address, otherwise say whose invitation it is. */
function SignedInChoice({ token, invitation }: { token: string; invitation: InvitationPreview }) {
  const { t } = useTranslation()
  const { state, acceptSwitched, signOut } = useAuth()
  const navigate = useNavigate()
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (state.status !== 'signedIn') return null
  const current = state.session.user.email
  if (current.toLowerCase() !== invitation.email.toLowerCase()) {
    return (
      <>
        <FormAlert tone="info">
          {t('invite.wrongAccount', { email: invitation.email, current })}
        </FormAlert>
        <Button variant="secondary" className="w-fit" onClick={() => void signOut()}>
          {t('invite.signOut')}
        </Button>
      </>
    )
  }
  const join = async () => {
    setBusy(true)
    setProblem(null)
    try {
      acceptSwitched(await invitationApi.acceptSignedIn(token))
      void navigate('/today', { replace: true })
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      setProblem(error.message)
      setBusy(false)
    }
  }
  return (
    <>
      <p className="m-0 text-body">{t('invite.signedInAs', { email: current })}</p>
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <Button disabled={busy} className="w-fit" onClick={() => void join()}>
        {t('invite.joinNow', { org: invitation.organization_name })}
      </Button>
    </>
  )
}

/**
 * `/accept-invite#token=…` from the invitation email (BUILD_SPEC R15). The token stays in memory only; the page
 * works signed in or out (DECISIONS D36).
 */
export function AcceptInvitePage() {
  const { t } = useTranslation()
  const token = useLinkToken()
  const { state } = useAuth()
  const preview = useQuery({
    queryKey: ['invitation', token],
    queryFn: () => invitationApi.preview(token ?? ''),
    enabled: Boolean(token),
    retry: false,
    staleTime: Infinity,
  })

  if (!token || preview.isError) {
    return (
      <AuthPage title={t('invite.invalidTitle')}>
        <FormAlert tone="error">{t('invite.invalid')}</FormAlert>
        <p className="m-0 text-small">
          <Link to="/signin">{t('auth.backToSignIn')}</Link>
        </p>
      </AuthPage>
    )
  }
  if (!preview.data || state.status === 'loading') {
    return (
      <AuthPage title={t('invite.loading')}>
        <p role="status" className="m-0 text-body text-ink-muted">
          {t('invite.loading')}
        </p>
      </AuthPage>
    )
  }
  const invitation = preview.data
  const role = t(`account.roles.${invitation.role}`)
  const intro = invitation.invited_by_name
    ? t('invite.intro', {
        inviter: invitation.invited_by_name,
        org: invitation.organization_name,
        role,
      })
    : t('invite.introNoInviter', { org: invitation.organization_name, role })

  let body
  if (state.status === 'signedIn') body = <SignedInChoice token={token} invitation={invitation} />
  else if (!invitation.account_exists)
    body = <NewAccountForm token={token} invitation={invitation} />
  else if (invitation.has_password)
    body = <ExistingAccountForm token={token} invitation={invitation} />
  else
    body = (
      <>
        <FormAlert tone="info">{t('invite.googleOnly', { email: invitation.email })}</FormAlert>
        <GoogleButton next="/today" divider={false} />
      </>
    )

  return (
    <AuthPage title={t('invite.title', { org: invitation.organization_name })} intro={intro}>
      {body}
    </AuthPage>
  )
}
