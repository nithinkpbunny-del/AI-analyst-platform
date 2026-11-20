import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { ActivityPage } from '../features/activity/ActivityPage'
import { AskPage } from '../features/ask/AskPage'
import { AcceptInvitePage } from '../features/auth/AcceptInvitePage'
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
import { SettingsPage } from '../features/settings/SettingsPage'
import { TeamPage } from '../features/team/TeamPage'
import { AppShell } from './AppShell'
import { RedirectIfSignedIn, RequireAuth } from './RequireAuth'

// The What-if screen loads on first visit: it brings the chart code and keeps the shell under its budget.
const ShopifyReturnPage = lazy(() =>
  import('../features/connections/ShopifyReturnPage').then((m) => ({
    default: m.ShopifyReturnPage,
  })),
)
const ZohoReturnPage = lazy(() =>
  import('../features/connections/ZohoReturnPage').then((m) => ({ default: m.ZohoReturnPage })),
)
// M13: the Connections and Dictionary screens load on first visit, with their strings (DECISIONS D99).
const ConnectionsPage = lazy(() =>
  import('../features/connections/ConnectionsPage').then((m) => ({ default: m.ConnectionsPage })),
)
const ConnectionDetailPage = lazy(() =>
  import('../features/connections/ConnectionDetailPage').then((m) => ({
    default: m.ConnectionDetailPage,
  })),
)
const SheetsReturnPage = lazy(() =>
  import('../features/connections/GoogleReturnPage').then((m) => ({
    default: m.GoogleReturnPage,
  })),
)
const DictionaryPage = lazy(() =>
  import('../features/dictionary/DictionaryPage').then((m) => ({ default: m.DictionaryPage })),
)
const WhatIfPage = lazy(() =>
  import('../features/whatif/WhatIfPage').then((m) => ({ default: m.WhatIfPage })),
)
// M10 screens load on first visit too (the shell stays under 200 KB).
const TodayPage = lazy(() =>
  import('../features/today/TodayPage').then((m) => ({ default: m.TodayPage })),
)
const BriefingsPage = lazy(() =>
  import('../features/briefings/BriefingsPage').then((m) => ({ default: m.BriefingsPage })),
)
const TasksPage = lazy(() =>
  import('../features/tasks/TasksPage').then((m) => ({ default: m.TasksPage })),
)
const ReportPage = lazy(() =>
  import('../features/reports/ReportPages').then((m) => ({ default: m.ReportPage })),
)
const SharedPage = lazy(() =>
  import('../features/reports/ReportPages').then((m) => ({ default: m.SharedPage })),
)
const SharedItemPage = lazy(() =>
  import('../features/reports/ReportPages').then((m) => ({ default: m.SharedItemPage })),
)

// M15: Dashboard and Goals load on first visit, with their own strings (DECISIONS D99).
const DashboardPage = lazy(() =>
  import('../features/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage })),
)
const GoalsPage = lazy(() =>
  import('../features/goals/GoalsPage').then((m) => ({ default: m.GoalsPage })),
)

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={null}>{children}</Suspense>
}

export function AppRoutes() {
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
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
      </Route>
      <Route element={<RequireAuth />}>
        <Route element={<AuthLayout />}>
          <Route path="/setup-two-factor" element={<RequiredSetupPage />} />
        </Route>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/today" replace />} />
          <Route
            path="/what-if"
            element={
              <Suspense fallback={null}>
                <WhatIfPage />
              </Suspense>
            }
          />
          <Route
            path="/today"
            element={
              <Lazy>
                <TodayPage />
              </Lazy>
            }
          />
          <Route
            path="/dashboard"
            element={
              <Lazy>
                <DashboardPage />
              </Lazy>
            }
          />
          <Route
            path="/goals"
            element={
              <Lazy>
                <GoalsPage />
              </Lazy>
            }
          />
          <Route
            path="/briefings"
            element={
              <Lazy>
                <BriefingsPage />
              </Lazy>
            }
          />
          <Route
            path="/tasks"
            element={
              <Lazy>
                <TasksPage />
              </Lazy>
            }
          />
          <Route
            path="/reports/:reportId"
            element={
              <Lazy>
                <ReportPage />
              </Lazy>
            }
          />
          <Route
            path="/shared"
            element={
              <Lazy>
                <SharedPage />
              </Lazy>
            }
          />
          <Route
            path="/shared/:shareId"
            element={
              <Lazy>
                <SharedItemPage />
              </Lazy>
            }
          />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/ask/:conversationId" element={<AskPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/team" element={<TeamPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route
            path="/connections"
            element={
              <Lazy>
                <ConnectionsPage />
              </Lazy>
            }
          />
          <Route
            path="/dictionary"
            element={
              <Lazy>
                <DictionaryPage />
              </Lazy>
            }
          />
          <Route
            path="/connections/google/return"
            element={
              <Lazy>
                <SheetsReturnPage />
              </Lazy>
            }
          />
          <Route
            path="/connections/shopify/return"
            element={
              <Lazy>
                <ShopifyReturnPage />
              </Lazy>
            }
          />
          <Route
            path="/connections/zoho/return"
            element={
              <Lazy>
                <ZohoReturnPage />
              </Lazy>
            }
          />
          <Route
            path="/connections/:connectionId"
            element={
              <Lazy>
                <ConnectionDetailPage />
              </Lazy>
            }
          />
          <Route path="*" element={<Navigate to="/today" replace />} />
        </Route>
      </Route>
    </Routes>
  )
}
