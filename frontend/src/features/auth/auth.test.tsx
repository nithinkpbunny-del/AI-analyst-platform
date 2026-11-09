import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { setAccessToken } from '../../lib/api'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import {
  apiError,
  csrfRoute,
  fakeApi,
  sessionResponse,
  signedOutRoutes,
} from '../../testing/fakeApi'

const PASSWORD = 'ledger-haldi-neel-42'

beforeEach(() => {
  window.localStorage.clear()
  setAccessToken(null)
  window.history.replaceState(null, '', '/')
})

function renderSignedOut(path: string) {
  return renderApp(path, { session: null })
}

async function fillSignIn(email = 'asha@urbancart.in', password = PASSWORD) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  return user
}

describe('guard', () => {
  it('sends signed-out visitors to sign in', async () => {
    fakeApi(signedOutRoutes)
    renderSignedOut('/today')
    expect(await screen.findByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
  })

  it('restores the session from the refresh cookie on load', async () => {
    fakeApi({ ...csrfRoute, 'POST /auth/refresh': { status: 200, body: sessionResponse() } })
    renderSignedOut('/goals')
    expect(await screen.findByRole('heading', { level: 1, name: 'Goals' })).toBeInTheDocument()
  })

  it('explains why the session ended', async () => {
    fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': apiError(
        401,
        'session_idle',
        'You were signed out after 30 minutes without activity. Sign in again.',
      ),
    })
    renderSignedOut('/today')
    expect(await screen.findByText(/signed out after 30 minutes/)).toBeInTheDocument()
  })

  it('shows the Viewer only what a Viewer may open', async () => {
    fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': {
        status: 200,
        body: sessionResponse({ role: 'viewer', permissions: ROLE_PERMISSIONS.viewer }),
      },
    })
    renderSignedOut('/today')
    const nav = await screen.findByTestId('side-nav')
    for (const name of ['Team', 'Connections', 'Activity']) {
      expect(within(nav).queryByRole('link', { name })).not.toBeInTheDocument()
    }
    expect(within(nav).getByRole('link', { name: 'Ask' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Settings' })).toBeInTheDocument()
  })
})

describe('sign in', () => {
  it('signs in and opens Today', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': { status: 200, body: sessionResponse() },
    })
    renderSignedOut('/signin')
    await fillSignIn()
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
    expect(api.body('POST /auth/login')).toEqual({
      email: 'asha@urbancart.in',
      password: PASSWORD,
    })
  })

  it('returns to the page the person wanted', async () => {
    fakeApi({ ...signedOutRoutes, 'POST /auth/login': { status: 200, body: sessionResponse() } })
    renderSignedOut('/briefings')
    await fillSignIn()
    expect(await screen.findByRole('heading', { level: 1, name: 'Briefings' })).toBeInTheDocument()
  })

  it('shows the generic error for wrong details', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': apiError(401, 'invalid_credentials', 'Invalid email or password.'),
    })
    renderSignedOut('/signin')
    await fillSignIn()
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
  })

  it('checks the fields before calling the API', async () => {
    const api = fakeApi(signedOutRoutes)
    renderSignedOut('/signin')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Sign in' }))
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription(
      'Enter a valid email address.',
    )
    expect(screen.getByLabelText('Password')).toHaveAttribute('aria-invalid', 'true')
    expect(api.count('POST /auth/login')).toBe(0)
  })

  it('offers to resend the confirmation link', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': apiError(403, 'email_not_verified', 'Confirm your email address first.'),
      'POST /auth/resend-verification': {
        status: 202,
        body: { status: 'accepted', message: 'ok' },
      },
    })
    renderSignedOut('/signin')
    const user = await fillSignIn()
    await user.click(await screen.findByRole('button', { name: 'Send the link again' }))
    expect(await screen.findByText(/We sent a new link to asha@urbancart.in/)).toBeInTheDocument()
    expect(api.body('POST /auth/resend-verification')).toEqual({ email: 'asha@urbancart.in' })
  })

  it('points to password reset when locked out', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': apiError(429, 'too_many_attempts', 'Too many sign-in attempts.'),
    })
    renderSignedOut('/signin')
    await fillSignIn()
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByRole('link', { name: 'Reset your password' })).toHaveAttribute(
      'href',
      '/forgot-password',
    )
  })

  it('can show the password', async () => {
    fakeApi(signedOutRoutes)
    renderSignedOut('/signin')
    const user = userEvent.setup()
    const input = await screen.findByLabelText('Password')
    expect(input).toHaveAttribute('type', 'password')
    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input).toHaveAttribute('type', 'text')
  })
})

describe('sign up', () => {
  async function fillSignUp(overrides: Partial<Record<string, string>> = {}, accept = true) {
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Your name'), overrides.name ?? 'Asha Rao')
    await user.type(screen.getByLabelText('Work email'), overrides.email ?? 'asha@urbancart.in')
    await user.type(screen.getByLabelText('Company name'), overrides.company ?? 'UrbanCart')
    await user.type(screen.getByLabelText('Password'), overrides.password ?? PASSWORD)
    if (accept) await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Create account' }))
  }

  it('creates the account and asks to check email', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/signup': {
        status: 202,
        body: { status: 'accepted', message: 'Check your email' },
      },
    })
    renderSignedOut('/signup')
    await fillSignUp()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Check your email' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/We sent a link to asha@urbancart.in/)).toBeInTheDocument()
    expect(api.body('POST /auth/signup')).toEqual({
      name: 'Asha Rao',
      email: 'asha@urbancart.in',
      organization_name: 'UrbanCart',
      password: PASSWORD,
      accept_terms: true,
    })
  })

  it('needs consent and a long enough password', async () => {
    const api = fakeApi(signedOutRoutes)
    renderSignedOut('/signup')
    await fillSignUp({ password: 'short' }, false)
    expect(screen.getByLabelText('Password')).toHaveAccessibleDescription(
      'Use at least 10 characters.',
    )
    expect(
      screen.getByText('Accept the privacy policy and terms to create an account.'),
    ).toBeInTheDocument()
    expect(api.count('POST /auth/signup')).toBe(0)
  })

  it('shows the server’s password advice next to the field', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/signup': apiError(422, 'weak_password', 'This password is too common.', {
        password: 'This password is too common. Choose one that is harder to guess.',
      }),
    })
    renderSignedOut('/signup')
    await fillSignUp({ password: 'password123' })
    await waitFor(() =>
      expect(screen.getByLabelText('Password')).toHaveAccessibleDescription(/too common/),
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('links to the privacy policy and terms', async () => {
    fakeApi(signedOutRoutes)
    renderSignedOut('/signup')
    expect(await screen.findByRole('link', { name: 'privacy policy' })).toHaveAttribute(
      'href',
      expect.stringMatching(/\/privacy$/),
    )
  })
})

describe('email links', () => {
  it('confirms the email from the link and removes the token from the address bar', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/verify-email': { status: 200, body: { status: 'ok', message: 'ok' } },
    })
    window.history.replaceState(null, '', '/verify-email#token=abcdefghijklmnop')
    renderSignedOut('/verify-email')
    expect(await screen.findByText(/Your email address is confirmed/)).toBeInTheDocument()
    expect(api.body('POST /auth/verify-email')).toEqual({ token: 'abcdefghijklmnop' })
    expect(api.count('POST /auth/verify-email')).toBe(1)
    expect(window.location.hash).toBe('')
  })

  it('offers a new link when the old one is used up', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/verify-email': apiError(400, 'invalid_link', 'Expired.'),
      'POST /auth/resend-verification': {
        status: 202,
        body: { status: 'accepted', message: 'ok' },
      },
    })
    window.history.replaceState(null, '', '/verify-email#token=abcdefghijklmnop')
    renderSignedOut('/verify-email')
    expect(
      await screen.findByText('This link has expired or was already used.'),
    ).toBeInTheDocument()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email'), 'asha@urbancart.in')
    await user.click(screen.getByRole('button', { name: 'Send a new link' }))
    expect(await screen.findByText(/we sent a new link/)).toBeInTheDocument()
  })

  it('sends a reset link without saying whether the account exists', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/forgot-password': {
        status: 202,
        body: { status: 'accepted', message: 'If this address has an account, we sent a link.' },
      },
    })
    renderSignedOut('/forgot-password')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'asha@urbancart.in')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect(await screen.findByRole('status')).toHaveTextContent('If this address has an account')
  })

  it('sets a new password from the reset link', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/reset-password': { status: 200, body: { status: 'ok', message: 'ok' } },
    })
    window.history.replaceState(null, '', '/reset-password#token=resettoken12345')
    renderSignedOut('/reset-password')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('New password'), 'a-brand-new-ledger-77')
    await user.click(screen.getByRole('button', { name: 'Set new password' }))
    expect(await screen.findByText(/Your password is changed/)).toBeInTheDocument()
    expect(api.body('POST /auth/reset-password')).toEqual({
      token: 'resettoken12345',
      password: 'a-brand-new-ledger-77',
    })
  })

  it('a reset page without a token asks for a new link', async () => {
    fakeApi(signedOutRoutes)
    renderSignedOut('/reset-password')
    expect(await screen.findByRole('link', { name: 'Ask for a new link' })).toBeInTheDocument()
  })
})

describe('sign out', () => {
  it('signs out from Settings with the CSRF header', async () => {
    const api = fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': { status: 200, body: sessionResponse() },
      'POST /auth/logout': { status: 204 },
      'GET /health': { status: 200, body: { status: 'ok', service: 'a', version: '1', env: 't' } },
      'GET /health/ready': { status: 200, body: { status: 'ok', checks: {} } },
    })
    renderSignedOut('/settings')
    const user = userEvent.setup()
    expect(await screen.findByText(/Signed in as Asha Rao/)).toBeInTheDocument()
    const main = screen.getByRole('main')
    await user.click(within(main).getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
    expect(api.header('POST /auth/logout', 'X-CSRF-Token')).toBe('csrf-1')
  })
})
