import { useTranslation } from 'react-i18next'
import { Navigate, Outlet, useLocation } from 'react-router'
import { useAuth } from '../lib/authContext'
import { brandIcon as BrandIcon } from './nav'

function SigningIn() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-dvh items-start bg-paper px-4 py-6 text-ink tablet:px-12 tablet:py-10">
      <p role="status" className="m-0 flex min-h-11 items-center gap-2 text-body text-ink-muted">
        <BrandIcon className="size-6 text-neel" aria-hidden strokeWidth={1.75} />
        {t('auth.loading')}
      </p>
    </div>
  )
}

/** App screens need a session. Signed-out visitors go to sign-in and come back afterwards. */
export function RequireAuth() {
  const { state } = useAuth()
  const location = useLocation()
  if (state.status === 'loading') return <SigningIn />
  if (state.status === 'signedOut') {
    return <Navigate to="/signin" replace state={{ from: location.pathname + location.search }} />
  }
  return <Outlet />
}

/** Sign-in and sign-up make no sense when already signed in. */
export function RedirectIfSignedIn() {
  const { state } = useAuth()
  if (state.status === 'loading') return <SigningIn />
  if (state.status === 'signedIn') return <Navigate to="/today" replace />
  return <Outlet />
}
