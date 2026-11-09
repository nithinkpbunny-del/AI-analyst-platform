import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthLayout } from '../features/auth/AuthLayout'
import { CheckEmailPage, ForgotPasswordPage } from '../features/auth/EmailPages'
import { SignInPage } from '../features/auth/SignInPage'
import { SignUpPage } from '../features/auth/SignUpPage'
import { AuthContext, type AuthContextValue } from '../lib/authContext'
import { dark, phone } from './variants'

const signedOut: AuthContextValue = {
  state: { status: 'signedOut', reason: null },
  signIn: async () => {},
  signOut: async () => {},
  clearReason: () => {},
}

const pages = {
  signin: <SignInPage />,
  signup: <SignUpPage />,
  'check-email': <CheckEmailPage />,
  'forgot-password': <ForgotPasswordPage />,
} as const

function AuthScreen({ screen }: { screen: keyof typeof pages }) {
  return (
    <div className="-m-4">
      <AuthContext.Provider value={signedOut}>
        <MemoryRouter
          initialEntries={[{ pathname: `/${screen}`, state: { email: 'asha@urbancart.in' } }]}
        >
          <Routes>
            <Route element={<AuthLayout />}>
              <Route path={`/${screen}`} element={pages[screen]} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
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
