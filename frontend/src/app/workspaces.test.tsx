import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { setAccessToken } from '../lib/api'
import { demoSession } from '../lib/demoSession'
import { ROLE_PERMISSIONS } from '../lib/permissions'
import { renderApp } from '../test-utils'
import { apiError, csrfRoute, fakeApi, sessionResponse } from '../testing/fakeApi'

const CHAIWALA = 'org-chaiwala'

const twoWorkspaces = sessionResponse({
  memberships: [
    ...demoSession.memberships,
    { organization_id: CHAIWALA, organization_name: 'Chaiwala Foods', role: 'viewer' },
  ],
})

const inChaiwala = {
  ...twoWorkspaces,
  organization: { ...demoSession.organization, id: CHAIWALA, name: 'Chaiwala Foods' },
  role: 'viewer' as const,
  permissions: ROLE_PERMISSIONS.viewer,
  access_token: 'access-2',
}

beforeEach(() => {
  window.localStorage.clear()
  setAccessToken(null)
  window.history.replaceState(null, '', '/')
})

describe('workspaces', () => {
  it('with one workspace the name is just a label', () => {
    fakeApi({})
    renderApp('/today')
    const nav = screen.getByTestId('side-nav')
    expect(within(nav).getAllByText('UrbanCart').length).toBeGreaterThan(0)
    expect(screen.queryByTestId('workspace-switcher')).not.toBeInTheDocument()
  })

  it('switches workspace, shows its menu and remembers it for next sign-in', async () => {
    const api = fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': { status: 200, body: twoWorkspaces },
      'POST /auth/switch-organization': { status: 200, body: inChaiwala },
    })
    renderApp('/goals', { session: null })
    const nav = await screen.findByTestId('side-nav')
    expect(within(nav).getByRole('link', { name: 'Team' })).toBeInTheDocument()
    const [switcher] = await screen.findAllByRole('button', {
      name: /^UrbanCart\s*Owner\s*, switch workspace$/,
    })
    await userEvent.click(switcher!)
    const list = await screen.findByRole('list', { name: 'Your workspaces' })
    expect(within(list).getByRole('button', { name: /UrbanCart/ })).toHaveAttribute(
      'aria-current',
      'true',
    )
    await userEvent.click(within(list).getByRole('button', { name: /Chaiwala Foods/ }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
    expect(api.body('POST /auth/switch-organization')).toEqual({ organization_id: CHAIWALA })
    // A Viewer there: no team or activity screens.
    await waitFor(() =>
      expect(within(nav).queryByRole('link', { name: 'Team' })).not.toBeInTheDocument(),
    )
    expect(within(nav).queryByRole('link', { name: 'Activity' })).not.toBeInTheDocument()
    expect(window.localStorage.getItem('analyst.lastWorkspace')).toBe(CHAIWALA)
  })

  it('says so when switching fails', async () => {
    fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': { status: 200, body: twoWorkspaces },
      'POST /auth/switch-organization': apiError(404, 'not_found', 'Not found.'),
    })
    renderApp('/today', { session: null })
    const [switcher] = await screen.findAllByRole('button', { name: /switch workspace/ })
    await userEvent.click(switcher!)
    await userEvent.click(await screen.findByRole('button', { name: /Chaiwala Foods/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Not found.')
  })

  it('the next sign-in asks for the remembered workspace', async () => {
    window.localStorage.setItem('analyst.lastWorkspace', CHAIWALA)
    const api = fakeApi({
      ...csrfRoute,
      'POST /auth/refresh': apiError(401, 'not_signed_in', 'Sign in to continue.'),
      'POST /auth/login': { status: 200, body: inChaiwala },
    })
    renderApp('/signin', { session: null })
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'asha@urbancart.in')
    await user.type(screen.getByLabelText('Password'), 'ledger-haldi-neel-42')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await screen.findByRole('heading', { level: 1, name: 'Today' })
    expect(api.body('POST /auth/login')).toMatchObject({ organization_id: CHAIWALA })
  })
})

describe('Settings: workspace', () => {
  it('Owners rename the workspace', async () => {
    const api = fakeApi({
      'PATCH /org': {
        status: 200,
        body: { ...demoSession.organization, name: 'UrbanCart India' },
      },
      'GET /auth/me': { status: 200, body: demoSession },
    })
    renderApp('/settings')
    const user = userEvent.setup()
    const name = screen.getByLabelText('Workspace name')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    await user.clear(name)
    await user.type(name, 'UrbanCart India')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Workspace settings saved.')).toBeInTheDocument()
    expect(api.body('PATCH /org')).toEqual({ name: 'UrbanCart India', timezone: 'Asia/Kolkata' })
  })

  it('others see it read-only, and Settings is in their menu', () => {
    fakeApi({})
    renderApp('/settings', {
      session: { ...demoSession, role: 'viewer', permissions: ROLE_PERMISSIONS.viewer },
    })
    expect(screen.queryByLabelText('Workspace name')).not.toBeInTheDocument()
    expect(screen.getByText('Only Owners can change these.')).toBeInTheDocument()
    const nav = screen.getByTestId('side-nav')
    expect(within(nav).getByRole('link', { name: 'Settings' })).toBeInTheDocument()
  })
})
