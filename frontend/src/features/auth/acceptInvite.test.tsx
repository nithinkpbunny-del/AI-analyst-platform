import { screen } from '@testing-library/react'
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
import { demoPreview } from '../../testing/teamFixtures'

const PASSWORD = 'ledger-haldi-neel-42'
const TOKEN = 'invitetoken1234567890'

beforeEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  setAccessToken(null)
  window.history.replaceState(null, '', `/accept-invite#token=${TOKEN}`)
})

const ravi = {
  user: { ...sessionResponse().user, id: 'u2', email: 'dev@urbancart.in', name: 'Dev Patel' },
  role: 'analyst' as const,
  permissions: ROLE_PERMISSIONS.analyst,
}

function open() {
  renderApp('/accept-invite', { session: null })
}

describe('accepting an invitation', () => {
  it('a new person creates their account and lands in the workspace', async () => {
    const api = fakeApi({
      ...signedOutRoutes,
      'POST /invitations/preview': { status: 200, body: demoPreview },
      'POST /invitations/accept': { status: 200, body: sessionResponse(ravi) },
    })
    open()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Join UrbanCart' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Asha Rao invited you to UrbanCart as Analyst.')).toBeInTheDocument()
    expect(window.location.hash).toBe('') // the token left the address bar
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Create account and join' }))
    expect(
      screen.getByText('Accept the privacy policy and terms to create an account.'),
    ).toBeVisible()
    await user.type(screen.getByLabelText('Your name'), 'Dev Patel')
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Create account and join' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
    expect(api.body('POST /invitations/accept')).toEqual({
      token: TOKEN,
      name: 'Dev Patel',
      password: PASSWORD,
      accept_terms: true,
    })
    expect(api.body('POST /invitations/preview')).toEqual({ token: TOKEN })
  })

  it('an existing account enters its password, then any second step', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /invitations/preview': {
        status: 200,
        body: { ...demoPreview, account_exists: true, has_password: true },
      },
      'POST /invitations/accept-sign-in': {
        status: 200,
        body: {
          status: 'two_factor_required',
          challenge_token: 'challenge-1',
          method: 'totp',
          email_hint: null,
          code_sent: false,
          expires_in: 900,
        },
      },
    })
    open()
    expect(await screen.findByText(/You already have an account/)).toBeInTheDocument()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Sign in and join' }))
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Two-step verification' }),
    ).toBeInTheDocument()
  })

  it('a wrong password says so', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /invitations/preview': {
        status: 200,
        body: { ...demoPreview, account_exists: true, has_password: true },
      },
      'POST /invitations/accept-sign-in': apiError(
        401,
        'invalid_credentials',
        'Invalid email or password.',
      ),
    })
    open()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Password'), 'not-my-password')
    await user.click(screen.getByRole('button', { name: 'Sign in and join' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
  })

  it('a Google-only account is sent to Google first', async () => {
    fakeApi({
      ...signedOutRoutes,
      'GET /auth/providers': { status: 200, body: { google: true, google_stand_in: false } },
      'POST /invitations/preview': {
        status: 200,
        body: { ...demoPreview, account_exists: true, has_password: false },
      },
    })
    open()
    expect(await screen.findByText(/uses Google sign-in/)).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Continue with Google' })).toBeInTheDocument()
  })

  it('signed in as the invited address: one click, then the new workspace', async () => {
    const api = fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': { status: 200, body: sessionResponse(ravi) },
      'POST /invitations/preview': { status: 200, body: demoPreview },
      'POST /invitations/accept-signed-in': {
        status: 200,
        body: sessionResponse(ravi),
      },
    })
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Join UrbanCart' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
    expect(api.count('POST /invitations/accept-signed-in')).toBe(1)
  })

  it('signed in as someone else: explains and offers to sign out', async () => {
    const api = fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': { status: 200, body: sessionResponse() },
      'POST /auth/logout': { status: 204 },
      'POST /invitations/preview': { status: 200, body: demoPreview },
    })
    open()
    expect(
      await screen.findByText(
        /This invitation is for dev@urbancart.in, but you are signed in as asha/,
      ),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('button', { name: 'Create account and join' })).toBeVisible()
    expect(api.count('POST /auth/logout')).toBe(1)
  })

  it('an expired or used link says what to do', async () => {
    fakeApi({
      ...signedOutRoutes,
      'POST /invitations/preview': apiError(400, 'invalid_link', 'Expired.'),
    })
    open()
    expect(
      await screen.findByRole('heading', { name: 'This invitation no longer works' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Ask the person who invited you/)).toBeInTheDocument()
  })

  it('without a token in the link', async () => {
    window.history.replaceState(null, '', '/accept-invite')
    fakeApi({ ...signedOutRoutes })
    open()
    expect(
      await screen.findByRole('heading', { name: 'This invitation no longer works' }),
    ).toBeInTheDocument()
  })
})
