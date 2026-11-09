import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { authApi } from '../../lib/authApi'
import { useAuth } from '../../lib/authContext'
import { AuthPage } from './AuthLayout'
import { useLinkToken } from './linkToken'

/** `/secure-account#token=…` from a "New sign-in" email: "This wasn't me" signs out every device. */
export function SecureAccountPage() {
  const { t } = useTranslation()
  const token = useLinkToken()
  const { signOut } = useAuth()
  const [result, setResult] = useState<'working' | 'done' | 'failed'>(token ? 'working' : 'failed')
  const started = useRef(false)
  useEffect(() => {
    if (!token || started.current) return
    started.current = true // single-use link
    authApi
      .secureAccount(token)
      .then(async () => {
        await signOut() // this browser's session ended on the server too
        setResult('done')
      })
      .catch(() => setResult('failed'))
  }, [token, signOut])

  return (
    <AuthPage title={t('auth.secureAccount.title')}>
      {result === 'working' ? (
        <p role="status" className="m-0 text-body text-ink-muted">
          {t('auth.secureAccount.checking')}
        </p>
      ) : null}
      {result === 'done' ? (
        <>
          <FormAlert tone="success">{t('auth.secureAccount.done')}</FormAlert>
          <p className="m-0 max-w-prose text-body">{t('auth.secureAccount.next')}</p>
          <Button asChild className="w-fit">
            <Link to="/forgot-password">{t('auth.secureAccount.reset')}</Link>
          </Button>
        </>
      ) : null}
      {result === 'failed' ? (
        <>
          <FormAlert tone="error">{t('auth.secureAccount.failed')}</FormAlert>
          <p className="m-0 max-w-prose text-body">{t('auth.secureAccount.failedNext')}</p>
          <Button asChild variant="secondary" className="w-fit">
            <Link to="/forgot-password">{t('auth.secureAccount.reset')}</Link>
          </Button>
        </>
      ) : null}
    </AuthPage>
  )
}
