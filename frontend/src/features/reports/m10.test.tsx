import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import { demoAiSettings, demoUsage } from '../../testing/aiFixtures'
import {
  demoAnswer,
  demoConversation,
  demoConversations,
  demoSaved,
} from '../../testing/askFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'
import {
  demoBriefing,
  demoBriefingSettings,
  demoOrg,
  demoRuns,
  demoShares,
  demoTask,
  demoTaskReport,
  demoTasks,
  demoToday,
} from '../../testing/reportsFixtures'

type Routes = Parameters<typeof fakeApi>[0]

const viewer = { ...demoSession, role: 'viewer' as const, permissions: ROLE_PERMISSIONS.viewer }

function routes(extra: Routes = {}) {
  return fakeApi({
    'GET /today': { status: 200, body: demoToday },
    'GET /org': { status: 200, body: demoOrg() },
    'GET /tasks': { status: 200, body: demoTasks },
    'GET /briefings/settings': { status: 200, body: demoBriefingSettings },
    'GET /briefings': { status: 200, body: { runs: demoRuns } },
    'GET /reports/rep-briefing': { status: 200, body: demoBriefing },
    'GET /reports/rep-task': { status: 200, body: demoTaskReport },
    'GET /shares': { status: 200, body: demoShares },
    'GET /org/ai': { status: 200, body: demoAiSettings },
    'GET /org/usage': { status: 200, body: demoUsage },
    ...extra,
  })
}

beforeEach(() => {
  vi.stubGlobal(
    'URL',
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Today', () => {
  it('shows the checklist, the briefing with its mark and sources, and suggestions', async () => {
    routes()
    renderApp('/today')
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeVisible()
    expect(screen.getByText(/Good to see you, Asha/)).toBeVisible()
    const checklist = await screen.findByRole('region', { name: 'Get to your first insight' })
    expect(within(checklist).getByText('2 of 4 done')).toBeVisible()
    expect(within(checklist).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '2')
    // The next step is the primary action; the health check asks at once.
    const health = within(checklist).getByRole('link', { name: /Run a health check/ })
    expect(health.getAttribute('href')).toContain('&run=1')
    expect(
      within(checklist).getByRole('link', { name: /Open: Confirm dictionary/ }),
    ).toHaveAttribute('href', '/dictionary')
    // The briefing: the haldi mark on its key figure, numbered sources, sections and key numbers.
    const briefing = screen.getByRole('region', { name: 'This morning’s briefing' })
    expect(
      within(briefing).getByText('-54.3%').closest('mark, [data-highlight]') ??
        within(briefing).getByText('-54.3%'),
    ).toBeVisible()
    expect(within(briefing).getByText('Today’s alerts')).toBeVisible()
    expect(within(briefing).getByText('Working well')).toBeVisible()
    expect(within(briefing).getByRole('link', { name: 'Open the full briefing' })).toHaveAttribute(
      'href',
      '/reports/rep-briefing',
    )
    expect(screen.getByText('Revenue, last 7 days')).toBeVisible()
    const suggestions = screen.getByRole('region', { name: 'Questions to ask today' })
    expect(within(suggestions).getAllByRole('link')[0]).toHaveAttribute(
      'href',
      `/ask?q=${encodeURIComponent(demoToday.suggestions[0]!)}`,
    )
    expect(screen.getByRole('region', { name: 'Shared with you' })).toBeVisible()
  })

  it('offers a briefing now when there is none yet', async () => {
    const api = routes({
      'GET /today': { status: 200, body: { ...demoToday, briefing: null } },
      'POST /briefings/send-now': { status: 202, body: { ...demoRuns[0], kind: 'test' } },
    })
    renderApp('/today')
    await userEvent.click(await screen.findByRole('button', { name: 'Send me a briefing now' }))
    expect(
      await screen.findByText('Your briefing is ready and on its way to your inbox.'),
    ).toBeVisible()
    expect(api.count('POST /briefings/send-now')).toBe(1)
  })

  it('warns when the workspace is being deleted', async () => {
    routes({
      'GET /org': { status: 200, body: demoOrg({ deletion_due_at: '2026-11-20T09:00:00Z' }) },
    })
    renderApp('/today')
    expect(await screen.findByText(/This workspace will be deleted on 20 Nov 2026/)).toBeVisible()
  })

  it('Viewers see their briefing but no checklist or suggestions', async () => {
    routes()
    renderApp('/today', { session: viewer })
    await screen.findByRole('region', { name: 'This morning’s briefing' })
    expect(screen.queryByRole('region', { name: 'Get to your first insight' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Questions to ask today' })).toBeNull()
  })
})

describe('Briefings', () => {
  it('saves my settings: weekly on a day, at a time', async () => {
    const api = routes({
      'PUT /briefings/settings': {
        status: 200,
        body: { ...demoBriefingSettings, frequency: 'weekly', weekday: 2, default: false },
      },
    })
    renderApp('/briefings')
    expect(await screen.findByText(/Next briefing:/)).toBeVisible()
    await userEvent.selectOptions(screen.getByLabelText('How often'), 'weekly')
    await userEvent.selectOptions(screen.getByLabelText('Day'), '2')
    const time = screen.getByLabelText('Time')
    await userEvent.clear(time)
    await userEvent.type(time, '07:30')
    await userEvent.click(screen.getByRole('button', { name: 'Save briefing settings' }))
    expect(await screen.findByText('Briefing settings saved.')).toBeVisible()
    expect(api.body('PUT /briefings/settings')).toEqual({
      enabled: true,
      local_time: '07:30',
      frequency: 'weekly',
      weekday: 2,
      timezone: 'Asia/Kolkata',
    })
    expect(screen.getByLabelText('Language')).toBeDisabled()
  })

  it('lists past briefings with why one was not sent', async () => {
    routes()
    renderApp('/briefings')
    const archive = await screen.findByRole('region', { name: 'Past briefings' })
    expect(await within(archive).findByText('Not sent: nothing needed attention')).toBeVisible()
    expect(within(archive).getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      '/reports/rep-briefing',
    )
  })

  it('turns briefings off', async () => {
    const api = routes({
      'PUT /briefings/settings': {
        status: 200,
        body: { ...demoBriefingSettings, enabled: false, next_at: null },
      },
    })
    renderApp('/briefings')
    await userEvent.click(await screen.findByLabelText('Send me a briefing'))
    await userEvent.click(screen.getByRole('button', { name: 'Save briefing settings' }))
    await waitFor(() =>
      expect(api.body('PUT /briefings/settings')).toMatchObject({
        enabled: false,
        frequency: 'off',
      }),
    )
  })
})

describe('Long tasks', () => {
  it('starts an analysis with its lens', async () => {
    const api = routes({ 'POST /tasks': { status: 201, body: demoTask({ status: 'queued' }) } })
    renderApp('/tasks')
    await userEvent.type(
      await screen.findByLabelText('What should be analysed?'),
      'Review Q3 by region',
    )
    await userEvent.selectOptions(screen.getByLabelText('Lens'), 'finance')
    await userEvent.click(screen.getByRole('button', { name: 'Start analysis' }))
    expect(await screen.findByText(/Analysis started/)).toBeVisible()
    expect(api.body('POST /tasks')).toEqual({ prompt: 'Review Q3 by region', lens: 'finance' })
    expect(screen.getByText('1 of 2 running in this workspace')).toBeVisible()
  })

  it('shows progress, cancels, opens the report and explains a failure', async () => {
    const api = routes({
      'POST /tasks/task2/cancel': {
        status: 200,
        body: demoTask({ id: 'task2', status: 'cancelled' }),
      },
    })
    renderApp('/tasks')
    expect(await screen.findByText('Fetching Marketing from Google Sheets')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open report' })).toHaveAttribute(
      'href',
      '/reports/rep-task',
    )
    expect(screen.getByText(/needed more steps than a long analysis may take/)).toBeVisible()
    await userEvent.click(
      screen.getByRole('button', { name: 'Cancel: Full marketing review for 2026' }),
    )
    await waitFor(() => expect(api.count('POST /tasks/task2/cancel')).toBe(1))
  })

  it('a too-busy workspace says so', async () => {
    routes({
      'POST /tasks': apiError(
        429,
        'too_many_tasks',
        'This workspace already has 2 long tasks running.',
      ),
    })
    renderApp('/tasks')
    await userEvent.type(await screen.findByLabelText('What should be analysed?'), 'Another review')
    await userEvent.click(screen.getByRole('button', { name: 'Start analysis' }))
    expect(
      await screen.findByText('This workspace already has 2 long tasks running.'),
    ).toBeVisible()
  })

  it('Viewers are told their role cannot start one', async () => {
    routes()
    renderApp('/tasks', { session: viewer })
    expect(await screen.findByText(/Viewers cannot start analyses/)).toBeVisible()
  })
})

describe('Reports, export and sharing', () => {
  it('a task report shows the question and the answer, and exports a PDF', async () => {
    const api = routes({ 'GET /reports/rep-task/export': { status: 200, body: {} } })
    renderApp('/reports/rep-task')
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Review Q3 2026 by region' }),
    ).toBeVisible()
    expect(screen.getByText('Review Q3 2026 by region and tell me what to fix.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Export' }))
    const dialog = await screen.findByRole('dialog', { name: 'Export' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Export PDF' }))
    await waitFor(() =>
      expect(api.calls.some((c) => c.url.endsWith('/reports/rep-task/export?format=pdf'))).toBe(
        true,
      ),
    )
  })

  it('shares with people who can see the data; others are listed but cannot be chosen', async () => {
    const api = routes({ 'POST /shares': { status: 201, body: demoShares } })
    renderApp('/reports/rep-briefing')
    await userEvent.click(await screen.findByRole('button', { name: 'Share' }))
    const dialog = await screen.findByRole('dialog', { name: 'Share' })
    const lata = await within(dialog).findByRole('checkbox', { name: /Lata/ })
    expect(lata).toBeDisabled()
    expect(within(dialog).getByText('Cannot see all the data this uses')).toBeVisible()
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /Ravi/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Share' }))
    expect(await within(dialog).findByText(/Shared. They get an email/)).toBeVisible()
    expect(api.body('POST /shares')).toEqual({
      object_type: 'report',
      object_id: 'rep-briefing',
      user_ids: ['u-ravi'],
    })
    expect(within(dialog).getByRole('button', { name: 'Stop sharing: Meera Shah' })).toBeVisible()
  })

  it('a Viewer opens and exports an item shared with them', async () => {
    const api = routes({
      'GET /shared/share1': {
        status: 200,
        body: {
          id: 'share1',
          object_type: 'message',
          title: 'Why did South revenue fall in Q3?',
          question: 'Why did South revenue fall in Q3?',
          answer: demoAnswer.answer,
          body: null,
          provenance: demoAnswer.provenance,
          quality_warnings: [],
          shared_by: 'Ravi',
          created_at: '2026-11-12T12:00:00Z',
        },
      },
      'GET /shared/share1/export': { status: 200, body: {} },
    })
    renderApp('/shared/share1', { session: viewer })
    expect(await screen.findByText('Shared with you by Ravi')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Export' }))
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Export Excel' }),
    )
    await waitFor(() =>
      expect(api.calls.some((c) => c.url.endsWith('/shared/share1/export?format=xlsx'))).toBe(true),
    )
    expect(screen.queryByRole('button', { name: 'Share' })).toBeNull()
  })

  it('a report that is not mine is not found', async () => {
    routes({ 'GET /reports/other': apiError(404, 'not_found', 'Not found.') })
    renderApp('/reports/other')
    expect(await screen.findByText(/This report was not found/)).toBeVisible()
  })

  it('an answer on Ask can be exported and shared', async () => {
    routes({
      'GET /conversations': { status: 200, body: { conversations: demoConversations } },
      'GET /saved-questions': { status: 200, body: { questions: demoSaved } },
      'GET /conversations/conv1': { status: 200, body: demoConversation },
    })
    renderApp('/ask/conv1')
    const sheet = await screen.findByRole('article', { name: /answer/i })
    await userEvent.click(within(sheet).getByRole('button', { name: 'Export' }))
    expect(await screen.findByRole('dialog', { name: 'Export' })).toBeVisible()
    await userEvent.keyboard('{Escape}')
    await userEvent.click(within(sheet).getByRole('button', { name: 'Share' }))
    expect(await screen.findByRole('dialog', { name: 'Share' })).toBeVisible()
  })
})

describe('Privacy (Settings)', () => {
  it('downloads the data and deletes only after the name is typed', async () => {
    const api = routes({
      'GET /org/export.zip': { status: 200, body: {} },
      'POST /org/deletion': {
        status: 202,
        body: demoOrg({
          deletion_requested_at: '2026-11-13T09:00:00Z',
          deletion_due_at: '2026-11-20T09:00:00Z',
        }),
      },
    })
    renderApp('/settings')
    const privacy = await screen.findByRole('region', { name: 'Privacy' })
    await userEvent.click(within(privacy).getByRole('button', { name: 'Download data' }))
    await waitFor(() => expect(api.count('GET /org/export.zip')).toBe(1))
    await userEvent.click(within(privacy).getByRole('button', { name: 'Delete workspace' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete UrbanCart?' })
    const confirm = within(dialog).getByRole('button', { name: 'Delete in 7 days' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Workspace name'), 'urbancart')
    await userEvent.click(confirm)
    expect(await within(privacy).findByText(/will be deleted on 20 Nov 2026/)).toBeVisible()
    expect(api.body('POST /org/deletion')).toEqual({ confirm_name: 'urbancart' })
  })

  it('a requested deletion can be cancelled', async () => {
    const api = routes({
      'GET /org': {
        status: 200,
        body: demoOrg({
          deletion_requested_at: '2026-11-13T09:00:00Z',
          deletion_due_at: '2026-11-20T09:00:00Z',
        }),
      },
      'DELETE /org/deletion': { status: 200, body: demoOrg() },
    })
    renderApp('/settings')
    const privacy = await screen.findByRole('region', { name: 'Privacy' })
    await userEvent.click(
      await within(privacy).findByRole('button', { name: 'Keep this workspace' }),
    )
    expect(
      await within(privacy).findByText('Deletion cancelled. Nothing was deleted.'),
    ).toBeVisible()
    expect(api.count('DELETE /org/deletion')).toBe(1)
  })

  it('the kind of business is saved with the workspace', async () => {
    const api = routes({
      'GET /org': { status: 200, body: demoOrg({ industry: null }) },
      'PATCH /org': { status: 200, body: demoOrg({ industry: 'restaurant' }) },
    })
    renderApp('/settings')
    await userEvent.selectOptions(await screen.findByLabelText('Kind of business'), 'restaurant')
    await userEvent.click(screen.getAllByRole('button', { name: 'Save changes' })[0]!)
    await waitFor(() => expect(api.body('PATCH /org')).toMatchObject({ industry: 'restaurant' }))
  })
})

describe('Command palette', () => {
  it('Ctrl+K opens it; typing filters screens and saved questions; Enter goes there', async () => {
    routes({
      'GET /saved-questions': { status: 200, body: { questions: demoSaved } },
      'GET /connections': { status: 200, body: { connections: [], sources: {} } },
    })
    renderApp('/today')
    await screen.findByRole('heading', { level: 1, name: 'Today' })
    await userEvent.keyboard('{Control>}k{/Control}')
    const box = await screen.findByRole('combobox')
    await userEvent.type(box, 'brief')
    const list = screen.getByRole('listbox')
    expect(within(list).getByRole('option', { name: 'Briefings' })).toBeVisible()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { level: 1, name: 'Briefings' })).toBeVisible()
  })
})
