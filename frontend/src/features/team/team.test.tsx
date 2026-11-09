import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import { apiError, fakeApi } from '../../testing/fakeApi'
import { demoInvitations, demoMembers } from '../../testing/teamFixtures'

beforeEach(() => {
  window.localStorage.clear()
})

function teamRoutes(extra: Record<string, unknown> = {}) {
  return fakeApi({
    'GET /team/members': { status: 200, body: { members: demoMembers } },
    'GET /team/invitations': { status: 200, body: { invitations: demoInvitations } },
    ...(extra as Record<string, { status: number; body?: unknown }>),
  })
}

describe('Team', () => {
  it('lists people with their role, two-step status and join date', async () => {
    teamRoutes()
    renderApp('/team')
    const people = await screen.findByRole('list', { name: 'People' })
    const rows = within(people).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]!).getByText('You')).toBeInTheDocument()
    // You cannot change your own role or remove yourself.
    expect(within(rows[0]!).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(rows[0]!).queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
    expect(within(rows[1]!).getByRole('combobox', { name: 'Role for Ravi Kumar' })).toHaveValue(
      'analyst',
    )
    expect(within(rows[1]!).getByText('Off')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('21 Oct 2026')).toBeInTheDocument()
  })

  it('changes a role and says what happened', async () => {
    const api = teamRoutes({
      'PATCH /team/members/m2': {
        status: 200,
        body: { ...demoMembers[1], role: 'owner', two_factor_enabled: false },
      },
    })
    renderApp('/team')
    const select = await screen.findByRole('combobox', { name: 'Role for Ravi Kumar' })
    await userEvent.selectOptions(select, 'owner')
    expect(await screen.findByText(/Ravi Kumar is now Owner/)).toBeInTheDocument()
    expect(screen.getByText(/will be asked to turn it on/)).toBeInTheDocument()
    expect(api.body('PATCH /team/members/m2')).toEqual({ role: 'owner' })
  })

  it('shows the API’s reason when a change is refused', async () => {
    teamRoutes({
      'PATCH /team/members/m2': apiError(
        409,
        'last_owner',
        'A workspace needs at least one Owner.',
      ),
    })
    renderApp('/team')
    await userEvent.selectOptions(
      await screen.findByRole('combobox', { name: 'Role for Ravi Kumar' }),
      'viewer',
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('needs at least one Owner')
  })

  it('removes someone only after confirming', async () => {
    const api = teamRoutes({ 'DELETE /team/members/m2': { status: 204 } })
    renderApp('/team')
    await userEvent.click(await screen.findByRole('button', { name: 'Remove Ravi Kumar' }))
    const dialog = await screen.findByRole('dialog', { name: 'Remove Ravi Kumar?' })
    expect(dialog).toHaveTextContent('signed out of it on every device')
    expect(api.count('DELETE /team/members/m2')).toBe(0)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Remove Ravi Kumar' }))
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Remove Ravi Kumar' }),
    )
    expect(await screen.findByText('Ravi Kumar was removed.')).toBeInTheDocument()
    expect(api.count('DELETE /team/members/m2')).toBe(1)
  })

  it('invites by email with a role', async () => {
    const api = teamRoutes({
      'POST /team/invitations': {
        status: 201,
        body: { ...demoInvitations[1], id: 'i3', email: 'neha@urbancart.in' },
      },
    })
    renderApp('/team')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Send invite' }))
    expect(screen.getByText('Enter a valid email address.')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Email'), 'neha@urbancart.in')
    await user.selectOptions(screen.getByLabelText('Role'), 'viewer')
    expect(screen.getByText(/Viewer: sees answers/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Send invite' }))
    expect(await screen.findByText('Invitation sent to neha@urbancart.in.')).toBeInTheDocument()
    expect(api.body('POST /team/invitations')).toEqual({
      email: 'neha@urbancart.in',
      role: 'viewer',
    })
    expect(screen.getByLabelText('Email')).toHaveValue('')
  })

  it('shows "already a member" next to the email field', async () => {
    teamRoutes({
      'POST /team/invitations': apiError(409, 'already_member', 'Already.', {
        email: 'Already a member.',
      }),
    })
    renderApp('/team')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ravi@urbancart.in')
    await user.click(screen.getByRole('button', { name: 'Send invite' }))
    expect(await screen.findByText('Already a member.')).toBeInTheDocument()
  })

  it('lists open invitations, resends and cancels them', async () => {
    const api = teamRoutes({
      'POST /team/invitations/i1/resend': {
        status: 200,
        body: { ...demoInvitations[0], expired: false },
      },
      'DELETE /team/invitations/i2': { status: 204 },
    })
    renderApp('/team')
    const pending = await screen.findByRole('list', { name: 'Invitations not accepted yet' })
    expect(within(pending).getByText('Link expired')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Send again to dev@urbancart.in' }))
    expect(await screen.findByText(/A new link was sent to dev@urbancart.in/)).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('button', { name: 'Cancel invitation to meera@urbancart.in' }),
    )
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel invitation' }))
    await waitFor(() => expect(api.count('DELETE /team/invitations/i2')).toBe(1))
    expect(await screen.findByText(/invitation to meera@urbancart.in was cancelled/)).toBeVisible()
  })

  it('is not offered to people who cannot manage the team', () => {
    fakeApi({})
    renderApp('/team', {
      session: { ...demoSession, role: 'analyst', permissions: ROLE_PERMISSIONS.analyst },
    })
    expect(screen.getByText(/Only Owners manage the team/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send invite' })).not.toBeInTheDocument()
  })
})
