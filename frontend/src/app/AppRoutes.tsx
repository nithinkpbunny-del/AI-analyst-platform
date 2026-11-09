import { Navigate, Route, Routes } from 'react-router'
import { AuthLayout } from '../features/auth/AuthLayout'
import {
  CheckEmailPage,
  ForgotPasswordPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from '../features/auth/EmailPages'
import { GoogleReturnPage, GoogleSignUpPage } from '../features/auth/GoogleSignIn'
import { SecureAccountPage } from '../features/auth/SecureAccountPage'
import { SignInPage } from '../features/auth/SignInPage'
import { SignUpPage } from '../features/auth/SignUpPage'
import { LoginSetupPage, RequiredSetupPage, TwoFactorPage } from '../features/auth/TwoFactorPages'
import { PlaceholderPage } from '../features/placeholder/PlaceholderPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { AppShell } from './AppShell'
import { adminNav, mainNav } from './nav'
import { RedirectIfSignedIn, RequireAuth } from './RequireAuth'

export function AppRoutes() {
  const placeholders = [...mainNav, ...adminNav].filter((item) => item.key !== 'settings')
  return (
    <Routes>
      <Route element={<AuthLayout />}>
        <Route element={<RedirectIfSignedIn />}>
          <Route path="/signin" element={<SignInPage />} />
          <Route path="/signup" element={<SignUpPage />} />
        </Route>
        {/* The second step of signing in, and the return from Google: no session yet. */}
        <Route path="/signin/verify" element={<TwoFactorPage />} />
        <Route path="/signin/setup" element={<LoginSetupPage />} />
        <Route path="/signin/google" element={<GoogleReturnPage />} />
        <Route path="/signup/google" element={<GoogleSignUpPage />} />
        {/* Reachable signed in or out: they come from email links. */}
        <Route path="/check-email" element={<CheckEmailPage />} />
        <Route path="/secure-account" element={<SecureAccountPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
      </Route>
      <Route element={<RequireAuth />}>
        <Route element={<AuthLayout />}>
          <Route path="/setup-two-factor" element={<RequiredSetupPage />} />
        </Route>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/today" replace />} />
          {placeholders.map((item) => (
            <Route key={item.key} path={item.path} element={<PlaceholderPage item={item} />} />
          ))}
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/today" replace />} />
        </Route>
      </Route>
    </Routes>
  )
}
