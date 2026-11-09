import type { Meta, StoryObj } from '@storybook/react-vite'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthLayout } from '../features/auth/AuthLayout'
import { CheckEmailPage, ForgotPasswordPage } from '../features/auth/EmailPages'
import { RecoveryCodes } from '../features/auth/RecoveryCodes'
import { SignInPage } from '../features/auth/SignInPage'
import { SignUpPage } from '../features/auth/SignUpPage'
import { LoginSetupPage, TwoFactorPage } from '../features/auth/TwoFactorPages'
import { AuthContext, staticAuthValue, type PendingChallenge } from '../lib/authContext'
import { dark, phone } from './variants'

const codeChallenge: PendingChallenge = {
  status: 'two_factor_required',
  challenge_token: 'story',
  method: 'totp',
  email_hint: 'as***@urbancart.in',
  code_sent: false,
  expires_in: 900,
  next: '/today',
}
const setupChallenge: PendingChallenge = {
  ...codeChallenge,
  status: 'two_factor_setup_required',
  method: null,
}

const RECOVERY = [
  'k7m2p-x9q4r',
  'b3n8t-w5j6h',
  'z4c7v-m2k9d',
  'p6q3s-t8y5n',
  'h9w4f-c3r7m',
  'e5u2x-j7b4k',
  'r8d6g-n3p9w',
  'y2t5h-q6m8c',
  'f4k9z-s7v3e',
  'w6b3n-d5x8u',
]

const pages = {
  signin: { element: <SignInPage />, challenge: null },
  signup: { element: <SignUpPage />, challenge: null },
  'check-email': { element: <CheckEmailPage />, challenge: null },
  'forgot-password': { element: <ForgotPasswordPage />, challenge: null },
  'signin/verify': { element: <TwoFactorPage />, challenge: codeChallenge },
  'signin/setup': { element: <LoginSetupPage />, challenge: setupChallenge },
} as const

function AuthScreen({ screen }: { screen: keyof typeof pages }) {
  const page = pages[screen]
  const [client] = useState(() => {
    const queries = new QueryClient()
    queries.setQueryData(['auth', 'providers'], { google: true, google_stand_in: false })
    return queries
  })
  return (
    <div className="-m-4">
      <QueryClientProvider client={client}>
        <AuthContext.Provider
          value={staticAuthValue({ status: 'signedOut', reason: null }, page.challenge)}
        >
          <MemoryRouter
            initialEntries={[{ pathname: `/${screen}`, state: { email: 'asha@urbancart.in' } }]}
          >
            <Routes>
              <Route element={<AuthLayout />}>
                <Route path={`/${screen}`} element={page.element} />
              </Route>
            </Routes>
          </MemoryRouter>
        </AuthContext.Provider>
      </QueryClientProvider>
    </div>
  )
}

const meta = {
  title: 'App/Sign-in screens',
  component: AuthScreen,
  args: { screen: 'signin' },
  argTypes: { screen: { control: 'select', options: Object.keys(pages) } },
} satisfies Meta<typeof AuthScreen>
export default meta
type Story = StoryObj<typeof meta>

export const SignIn: Story = {}
export const SignInDark: Story = { ...dark }
export const SignInPhone: Story = { ...phone }
export const SignUp: Story = { args: { screen: 'signup' } }
export const SignUpPhone: Story = { args: { screen: 'signup' }, ...phone }
export const CheckEmail: Story = { args: { screen: 'check-email' } }
export const ForgotPassword: Story = { args: { screen: 'forgot-password' } }
export const TwoStepCode: Story = { args: { screen: 'signin/verify' } }
export const TwoStepCodePhone: Story = { args: { screen: 'signin/verify' }, ...phone }
export const TwoStepSetup: Story = { args: { screen: 'signin/setup' } }
export const TwoStepSetupDark: Story = { args: { screen: 'signin/setup' }, ...dark }

/** The one-time view of new recovery codes (after set-up, or "Create new recovery codes"). */
export const RecoveryCodesList: StoryObj = {
  render: () => (
    <div className="max-w-[440px]">
      <RecoveryCodes codes={RECOVERY} email="asha@urbancart.in" onDone={() => {}} />
    </div>
  ),
}
export const RecoveryCodesPhone: StoryObj = { ...RecoveryCodesList, ...phone }
