import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import type { AuditEntry } from '../../lib/teamApi'
import { renderApp } from '../../test-utils'
import { fakeApi } from '../../testing/fakeApi'
import { demoAudit, demoMembers } from '../../testing/teamFixtures'
import { describeEntry } from './describe'

beforeEach(() => {
  window.localStorage.clear()
})

function lastQuery(api: ReturnType<typeof fakeApi>): URLSearchParams {
  const call = [...api.calls].reverse().find((c) => c.key === 'GET /audit')
  return new URL(String(call?.url)).searchParams
}

describe('Activity', () => {
  it('shows entries in plain words, in the workspace time zone', async () => {
    fakeApi({
      'GET /audit': {
        status: 200,
        body: { entries: demoAudit, next_cursor: null, scope: 'workspace' },
      },
      'GET /team/members': { status: 200, body: { members: demoMembers } },
    })
    renderApp('/activity')
    const list = await screen.findByRole('list', { name: 'Activity' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Changed ravi@urbancart.in from Viewer to Analyst')
    expect(rows[0]).toHaveTextContent('30 Oct 2026, 10:42 am')
    expect(rows[3]).toHaveTextContent('Sign-in failed (wrong password)')
    expect(rows[3]).toHaveTextContent('198.51.100.7 · Safari on iPhone')
    // Owners can filter by person.
    expect(await screen.findByLabelText('Person')).toBeInTheDocument()
  })

  it('filters by person, kind of activity and dates', async () => {
    const api = fakeApi({
      'GET /audit': {
        status: 200,
        body: { entries: demoAudit, next_cursor: null, scope: 'workspace' },
      },
      'GET /team/members': { status: 200, body: { members: demoMembers } },
    })
    renderApp('/activity')
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText('Person'), 'u2')
    await user.selectOptions(screen.getByLabelText('What'), 'team')
    await user.type(screen.getByLabelText('From'), '2026-10-01')
    await waitFor(() => {
      const query = lastQuery(api)
      expect(Object.fromEntries(query)).toEqual({
        user_id: 'u2',
        action: 'team',
        from: '2026-10-01',
      })
    })
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByLabelText('Person')).toHaveValue('')
    expect(screen.getByLabelText('What')).toHaveValue('')
    expect(screen.getByLabelText('From')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeDisabled()
  })

  it('loads older pages', async () => {
    const api = fakeApi({
      'GET /audit': (_, url) =>
        url.searchParams.get('cursor') === 'page-2'
          ? {
              status: 200,
              body: { entries: [demoAudit[4]], next_cursor: null, scope: 'workspace' },
            }
          : {
              status: 200,
              body: { entries: demoAudit.slice(0, 4), next_cursor: 'page-2', scope: 'workspace' },
            },
      'GET /team/members': { status: 200, body: { members: demoMembers } },
    })
    renderApp('/activity')
    await userEvent.click(await screen.findByRole('button', { name: 'Show older activity' }))
    expect(await screen.findByText('Turned on two-step verification')).toBeInTheDocument()
    expect(lastQuery(api).get('cursor')).toBe('page-2')
    expect(screen.queryByRole('button', { name: 'Show older activity' })).not.toBeInTheDocument()
  })

  it('tells Analysts they see only their own activity', async () => {
    const api = fakeApi({
      'GET /audit': {
        status: 200,
        body: { entries: [demoAudit[3]], next_cursor: null, scope: 'own' },
      },
    })
    renderApp('/activity', {
      session: { ...demoSession, role: 'analyst', permissions: ROLE_PERMISSIONS.analyst },
    })
    expect(await screen.findByText(/You see your own activity/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Person')).not.toBeInTheDocument()
    expect(api.count('GET /team/members')).toBe(0)
  })

  it('exports the filtered log as a CSV download', async () => {
    const api = fakeApi({
      'GET /audit': {
        status: 200,
        body: { entries: demoAudit, next_cursor: null, scope: 'workspace' },
      },
      'GET /team/members': { status: 200, body: { members: demoMembers } },
      'GET /audit/export.csv': { status: 200, body: 'Time,Person\n' },
    })
    const createUrl = vi.fn(() => 'blob:activity')
    const revokeUrl = vi.fn()
    vi.stubGlobal(
      'URL',
      Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }),
    )
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    renderApp('/activity')
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText('What'), 'auth.login')
    await user.click(screen.getByRole('button', { name: 'Export CSV' }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    const call = api.calls.find((c) => c.key === 'GET /audit/export.csv')
    expect(new URL(String(call?.url)).searchParams.get('action')).toBe('auth.login')
    expect(revokeUrl).toHaveBeenCalledWith('blob:activity')
    click.mockRestore()
  })
})

describe('describeEntry', () => {
  const t = i18next.t.bind(i18next)
  const entry = (action: string, details: Record<string, unknown> = {}): AuditEntry => ({
    ...demoAudit[0]!,
    action,
    details,
  })

  it('uses the details', () => {
    expect(describeEntry(t, entry('team.invite_sent', { email: 'a@b.in', role: 'viewer' }))).toBe(
      'Invited a@b.in as Viewer',
    )
    expect(describeEntry(t, entry('team.invite_accepted', { role: 'analyst' }))).toBe(
      'Joined as Analyst',
    )
  })

  it('falls back to the action name for actions it does not know', () => {
    expect(describeEntry(t, entry('connections.added'))).toBe('connections.added')
  })

  it('names connections, files and people (M6)', () => {
    expect(describeEntry(t, entry('connection.added', { name: 'Sales', type: 'file' }))).toBe(
      'Added the connection Sales',
    )
    expect(describeEntry(t, entry('connection.file_uploaded', { name: 'Sales', version: 3 }))).toBe(
      'Uploaded version 3 of Sales',
    )
    // Names are shown as they are, never looked up as roles.
    expect(
      describeEntry(t, entry('connection.renamed', { from: 'owner', to: 'Orders v2.0' })),
    ).toBe('Renamed the connection owner to Orders v2.0')
    expect(describeEntry(t, entry('connection.version_used', { name: 'Sales', version: 1 }))).toBe(
      'Switched Sales to version 1',
    )
    expect(describeEntry(t, entry('connection.access_changed', { user_name: 'Ravi Kumar' }))).toBe(
      'Changed which connections Ravi Kumar sees',
    )
  })
})
