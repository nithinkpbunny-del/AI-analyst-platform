import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { setAccessToken } from '../../lib/api'
import type { ChallengeResponse } from '../../lib/authApi'
import { demoSession } from '../../lib/demoSession'
import { renderApp } from '../../test-utils'
import {
  apiError,
  csrfRoute,
  fakeApi,
  sessionResponse,
  signedOutRoutes,
} from '../../testing/fakeApi'

const PASSWORD = 'ledger-haldi-neel-42'
const RECOVERY = Array.from({ length: 10 }, (_, i) => `abcde-fgh${i}k`)

beforeEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  setAccessToken(null)
  window.history.replaceState(null, '', '/')
})

function challenge(overrides: Partial<ChallengeResponse> = {}): ChallengeResponse {
  return {
    status: 'two_factor_required',
    challenge_token: 'challenge-1',
    method: 'totp',
    email_hint: 'as***@urbancart.in',
    code_sent: false,
    expires_in: 900,
    ...overrides,
  }
}

async function signIn(path = '/signin') {
  renderApp(path, { session: null })
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Email'), 'asha@urbancart.in')
  await user.type(screen.getByLabelText('Password'), PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  return user
}

describe('second step of signing in', () => {
  it('asks for the app code and then opens the page the person wanted', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': { status: 200, body: challenge() },
      'POST /auth/login/verify': { status: 200, body: sessionResponse() },
    })
    const user = await signIn('/briefings')
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Two-step verification' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Open your authenticator app/)).toBeInTheDocument()
    const field = screen.getByLabelText('6-digit code')
    expect(field).toHaveAttribute('autocomplete', 'one-time-code')
    expect(field).toHaveAttribute('inputmode', 'numeric')
    await user.type(field, '123 456')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Briefings' })).toBeInTheDocument()
    expect(api.body('POST /auth/login/verify')).toEqual({
      challenge_token: 'challenge-1',
      code: '123456',
    })
  })

  it('shows a wrong code next to the field', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': { status: 200, body: challenge() },
      'POST /auth/login/verify': apiError(
        400,
        'invalid_code',
        'That code is not right. Check it and try again.',
      ),
    })
    const user = await signIn()
    await user.type(await screen.findByLabelText('6-digit code'), '111111')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() =>
      expect(screen.getByLabelText('6-digit code')).toHaveAccessibleDescription(
        'That code is not right. Check it and try again.',
      ),
    )
  })

  it('offers to sign in again when the attempt expired', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': { status: 200, body: challenge() },
      'POST /auth/login/verify': apiError(401, 'challenge_expired', 'Too many wrong codes.'),
    })
    const user = await signIn()
    await user.type(await screen.findByLabelText('6-digit code'), '111111')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many wrong codes.')
    expect(within(alert).getByRole('link', { name: 'Sign in again' })).toHaveAttribute(
      'href',
      '/signin',
    )
  })

  it('accepts a recovery code', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': { status: 200, body: challenge() },
      'POST /auth/login/verify': { status: 200, body: sessionResponse() },
    })
    const user = await signIn()
    await user.click(await screen.findByRole('button', { name: 'Use a recovery code instead' }))
    await user.type(screen.getByLabelText('Recovery code'), 'abcde-fghjk')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
    expect(api.body('POST /auth/login/verify')).toEqual({
      challenge_token: 'challenge-1',
      code: 'abcdefghjk',
    })
  })

  it('can email a new code', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': {
        status: 200,
        body: challenge({ method: 'email', code_sent: true }),
      },
      'POST /auth/login/resend-code': {
        status: 200,
        body: challenge({ method: 'email', code_sent: true }),
      },
    })
    const user = await signIn()
    expect(await screen.findByText(/We sent a 6-digit code to as\*\*\*@urbancart.in/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Send a new code' }))
    expect(await screen.findByRole('status')).toHaveTextContent('We sent a new code.')
    expect(api.count('POST /auth/login/resend-code')).toBe(1)
  })

  it('sends people without a pending sign-in back to sign in', async () => {
    fakeApi(signedOutRoutes)
    renderApp('/signin/verify', { session: null })
    expect(await screen.findByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
  })
})

describe('an Owner sets up two-step verification while signing in', () => {
  it('uses an authenticator app, shows recovery codes, then signs in', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': {
        status: 200,
        body: challenge({ status: 'two_factor_setup_required', method: null }),
      },
      'POST /auth/login/setup': {
        status: 200,
        body: {
          method: 'totp',
          secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
          otpauth_uri: 'otpauth://totp/Analyst:asha?secret=JBSW',
          qr: ['1010', '0101', '1100', '0011'],
          email_hint: null,
        },
      },
      'POST /auth/login/setup/confirm': {
        status: 200,
        body: { ...sessionResponse(), recovery_codes: RECOVERY },
      },
    })
    const user = await signIn()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Turn on two-step verification' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Authenticator app/ })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(
      await screen.findByRole('img', { name: 'QR code for your authenticator app' }),
    ).toBeInTheDocument()
    expect(screen.getByText('JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP')).toBeInTheDocument()
    expect(api.body('POST /auth/login/setup')).toEqual({
      challenge_token: 'challenge-1',
      method: 'totp',
    })
    await user.type(screen.getByLabelText('6-digit code'), '123456')
    await user.click(screen.getByRole('button', { name: 'Turn on two-step verification' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Save your recovery codes' }),
    ).toBeInTheDocument()
    const list = screen.getByRole('list', { name: 'Recovery codes' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(10)
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(
      screen.getByRole('checkbox', { name: 'I have saved my recovery codes' }),
    ).toHaveAttribute('aria-invalid', 'true')
    await user.click(screen.getByRole('checkbox', { name: 'I have saved my recovery codes' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
  })

  it('can use email codes instead', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/login': {
        status: 200,
        body: challenge({ status: 'two_factor_setup_required', method: null }),
      },
      'POST /auth/login/setup': {
        status: 200,
        body: {
          method: 'email',
          secret: null,
          otpauth_uri: null,
          qr: null,
          email_hint: 'as***@urbancart.in',
        },
      },
      'POST /auth/login/setup/resend-code': {
        status: 202,
        body: { status: 'accepted', message: 'We sent a new code.' },
      },
    })
    const user = await signIn()
    await user.click(await screen.findByRole('radio', { name: /Code by email/ }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText(/We sent a 6-digit code to as\*\*\*@urbancart.in/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Send a new code' }))
    await waitFor(() => expect(api.count('POST /auth/login/setup/resend-code')).toBe(1))
    await user.click(screen.getByRole('button', { name: 'Turn on two-step verification' }))
    expect(screen.getByLabelText('6-digit code')).toHaveAccessibleDescription('Enter the code.')
  })
})

describe('signed-in Owner without two-step verification', () => {
  it('is sent to set it up before anything else', async () => {
    fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': {
        status: 200,
        body: sessionResponse({
          two_factor: {
            enabled: false,
            method: null,
            required: true,
            recovery_codes_remaining: 0,
          },
        }),
      },
    })
    renderApp('/goals', { session: null })
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Turn on two-step verification' }),
    ).toBeInTheDocument()
  })
})

describe('Google sign-in', () => {
  it('shows the button only when Google is available', async () => {
    fakeApi({
      ...signedOutRoutes,
      'GET /auth/providers': { status: 200, body: { google: true, google_stand_in: true } },
    })
    renderApp('/signin', { session: null })
    const link = await screen.findByRole('link', { name: 'Continue with Google' })
    expect(link).toHaveAttribute('href', 'http://localhost:8000/auth/google/start')
    expect(screen.getByText(/Google is simulated on this computer/)).toBeInTheDocument()
  })

  it('stays hidden when the API has no Google sign-in', async () => {
    fakeApi({
      ...signedOutRoutes,
      'GET /auth/providers': { status: 200, body: { google: false, google_stand_in: false } },
    })
    renderApp('/signup', { session: null })
    await screen.findByRole('heading', { level: 1, name: 'Create your account' })
    expect(screen.queryByRole('link', { name: 'Continue with Google' })).not.toBeInTheDocument()
  })

  it('explains a failed Google sign-in', async () => {
    fakeApi(signedOutRoutes)
    renderApp('/signin?google=google_cancelled', { session: null })
    expect(await screen.findByRole('alert')).toHaveTextContent('Google sign-in was cancelled.')
  })

  it('finishes with the token from the return link', async () => {
    window.history.replaceState(null, '', '/signin/google#token=google-token-1')
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/google/finish': { status: 200, body: challenge() },
    })
    renderApp('/signin/google', { session: null })
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Two-step verification' }),
    ).toBeInTheDocument()
    expect(api.body('POST /auth/google/finish')).toEqual({ token: 'google-token-1' })
    expect(window.location.hash).toBe('')
  })

  it('lets a new person name their workspace', async () => {
    window.history.replaceState(null, '', '/signup/google#token=google-token-2')
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/google/signup-info': {
        status: 200,
        body: { email: 'asha@urbancart.in', name: 'Asha Rao' },
      },
      'POST /auth/google/signup': {
        status: 200,
        body: challenge({ status: 'two_factor_setup_required', method: null }),
      },
    })
    renderApp('/signup/google', { session: null })
    const user = userEvent.setup()
    expect(await screen.findByText(/Google confirmed asha@urbancart.in/)).toBeInTheDocument()
    expect(screen.getByLabelText('Your name')).toHaveValue('Asha Rao')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(screen.getByLabelText('Company name')).toHaveAttribute('aria-invalid', 'true')
    await user.type(screen.getByLabelText('Company name'), 'UrbanCart')
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Turn on two-step verification' }),
    ).toBeInTheDocument()
    expect(api.body('POST /auth/google/signup')).toEqual({
      token: 'google-token-2',
      name: 'Asha Rao',
      organization_name: 'UrbanCart',
      accept_terms: true,
    })
  })
})

describe('"This wasn\'t me"', () => {
  it('signs out every device and suggests a new password', async () => {
    window.history.replaceState(null, '', '/secure-account#token=secure-token-1')
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /auth/secure-account': { status: 200, body: { status: 'ok', message: 'Done.' } },
      'POST /auth/logout': { status: 204 },
    })
    renderApp('/secure-account', { session: null })
    expect(await screen.findByText('Every device is signed out, including this one.')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Choose a new password' })).toHaveAttribute(
      'href',
      '/forgot-password',
    )
    expect(api.body('POST /auth/secure-account')).toEqual({ token: 'secure-token-1' })
  })

  it('explains an expired link', async () => {
    window.history.replaceState(null, '', '/secure-account#token=secure-token-2')
    fakeApi({
      ...signedOutRoutes,
      'POST /auth/secure-account': apiError(400, 'invalid_link', 'Expired.'),
    })
    renderApp('/secure-account', { session: null })
    expect(await screen.findByRole('alert')).toHaveTextContent('This link has expired')
  })
})

describe('Settings: security', () => {
  const health = {
    'GET /health': {
      status: 200,
      body: { status: 'ok', service: 'analyst-api', version: '0.1.0', env: 'test' },
    },
    'GET /health/ready': { status: 200, body: { status: 'ok', checks: { database: 'ok' } } },
  }
  const sessions = {
    sessions: [
      {
        id: 's1',
        current: true,
        browser: 'Chrome on macOS',
        ip: '10.0.0.1',
        created_at: new Date().toISOString(),
        last_seen_at: new Date().toISOString(),
      },
      {
        id: 's2',
        current: false,
        browser: 'Firefox on Linux',
        ip: '10.0.0.2',
        created_at: new Date().toISOString(),
        last_seen_at: new Date().toISOString(),
      },
    ],
  }

  it('shows the 2FA status and keeps "Turn off" away from Owners', async () => {
    fakeApi({ ...health, 'GET /auth/sessions': { status: 200, body: sessions } })
    renderApp('/settings')
    const section = await screen.findByRole('region', { name: 'Two-step verification' })
    expect(within(section).getByText('On: authenticator app')).toBeInTheDocument()
    expect(within(section).getByText('10 of 10 recovery codes left')).toBeInTheDocument()
    expect(within(section).getByText('Owners must keep two-step verification on.')).toBeVisible()
    expect(within(section).queryByRole('button', { name: 'Turn off' })).not.toBeInTheDocument()
  })

  it('lets an Analyst turn it off with a current code', async () => {
    const api = fakeApi({
      ...health,
      'GET /auth/sessions': { status: 200, body: sessions },
      'POST /auth/2fa/disable': { status: 200, body: { status: 'ok', message: 'Off.' } },
      'GET /auth/me': { status: 200, body: demoSession },
    })
    renderApp('/settings', {
      session: {
        ...demoSession,
        role: 'analyst',
        two_factor: { ...demoSession.two_factor, required: false },
      },
    })
    const user = userEvent.setup()
    const section = await screen.findByRole('region', { name: 'Two-step verification' })
    await user.click(within(section).getByRole('button', { name: 'Turn off' }))
    await user.type(within(section).getByLabelText('Code from your current method'), '123456')
    await user.click(within(section).getByRole('button', { name: 'Turn off' }))
    expect(await within(section).findByText('Two-step verification is off.')).toBeVisible()
    expect(api.body('POST /auth/2fa/disable')).toEqual({ code: '123456' })
  })

  it('needs a current code before changing the method', async () => {
    const api = fakeApi({ ...health, 'GET /auth/sessions': { status: 200, body: sessions } })
    renderApp('/settings')
    const user = userEvent.setup()
    const section = await screen.findByRole('region', { name: 'Two-step verification' })
    await user.click(within(section).getByRole('button', { name: 'Change method' }))
    const next = within(section).getByRole('button', { name: 'Continue' })
    expect(next).toBeDisabled()
    await user.type(within(section).getByLabelText('Code from your current method'), '654321')
    expect(next).toBeEnabled()
    expect(api.count('POST /auth/2fa/setup')).toBe(0)
  })

  it('creates new recovery codes', async () => {
    fakeApi({
      ...health,
      'GET /auth/sessions': { status: 200, body: sessions },
      'POST /auth/2fa/recovery-codes': { status: 200, body: { recovery_codes: RECOVERY } },
    })
    renderApp('/settings')
    const user = userEvent.setup()
    const section = await screen.findByRole('region', { name: 'Two-step verification' })
    await user.click(within(section).getByRole('button', { name: 'Create new recovery codes' }))
    await user.type(within(section).getByLabelText('Code from your current method'), '123456')
    await user.click(within(section).getByRole('button', { name: 'Create new recovery codes' }))
    expect(await within(section).findByRole('list', { name: 'Recovery codes' })).toBeVisible()
  })

  it('lists devices and signs out another one', async () => {
    const api = fakeApi({
      ...health,
      'GET /auth/sessions': { status: 200, body: sessions },
      'DELETE /auth/sessions/s2': { status: 204 },
      'POST /auth/sessions/revoke-others': { status: 200, body: { sessions_revoked: 1 } },
    })
    renderApp('/settings')
    const user = userEvent.setup()
    const section = await screen.findByRole('region', { name: 'Signed-in devices' })
    expect(await within(section).findByText('Firefox on Linux')).toBeInTheDocument()
    expect(within(section).getByText('This device')).toBeInTheDocument()
    await user.click(within(section).getByRole('button', { name: 'Sign out Firefox on Linux' }))
    await waitFor(() => expect(api.count('DELETE /auth/sessions/s2')).toBe(1))
    await user.click(within(section).getByRole('button', { name: 'Sign out all other devices' }))
    expect(await within(section).findByText('Signed out 1 other devices.')).toBeVisible()
  })
})
