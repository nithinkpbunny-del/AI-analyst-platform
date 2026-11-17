import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PickerConfig } from '../../lib/connectionsApi'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import {
  demoAccess,
  demoConnectionList,
  demoMarketing,
  demoOrders,
  demoOrdersDetail,
  demoRazorpay,
  demoShopify,
  demoZoho,
  demoDatabase,
  demoDatabaseDetail,
  demoDatabaseTables,
  demoDatasetPreview,
  demoTargets,
} from '../../testing/connectionFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'

const analyst = {
  ...demoSession,
  role: 'analyst' as const,
  permissions: ROLE_PERMISSIONS.analyst,
}

const standInPicker: PickerConfig = {
  access_token: 'standin-at.x',
  expires_in: 3599,
  developer_key: 'stand-in',
  app_id: '0',
  stand_in: true,
  stand_in_files: [
    { id: 'standin-sample-outlets', name: 'Outlet sales (sample)' },
    { id: 'standin-urbancart-marketing', name: 'UrbanCart marketing plan' },
  ],
}

type Routes = Parameters<typeof fakeApi>[0]

function routes(extra: Routes = {}) {
  return fakeApi({
    'GET /connections': { status: 200, body: demoConnectionList },
    'GET /connections/access': { status: 200, body: { people: demoAccess } },
    'GET /connections/c1': { status: 200, body: demoOrdersDetail },
    'GET /connections/c1/datasets/d1/preview': { status: 200, body: demoDatasetPreview },
    ...extra,
  })
}

const assign = vi.fn()

beforeEach(() => {
  window.localStorage.clear()
  assign.mockReset()
  vi.stubGlobal('location', { ...window.location, assign })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Connections list', () => {
  it('shows every connection with status, size, data notes and fixes', async () => {
    routes()
    renderApp('/connections')
    const orders = await screen.findByRole('article', { name: 'UrbanCart orders' })
    expect(within(orders).getByText('Live')).toBeInTheDocument()
    expect(within(orders).getByText('Excel or CSV file')).toBeInTheDocument()
    expect(within(orders).getByText('1 table · 28,540 rows')).toBeInTheDocument()
    expect(within(orders).getByText('2 data notes')).toBeInTheDocument()
    expect(within(orders).getByText('Updated 30 Oct 2026')).toBeInTheDocument()
    expect(within(orders).getByRole('link', { name: 'Open UrbanCart orders' })).toHaveAttribute(
      'href',
      '/connections/c1',
    )
    const targets = screen.getByRole('article', { name: 'Regional targets' })
    expect(within(targets).getByText('Needs sign-in')).toBeInTheDocument()
    expect(within(targets).getByText(/Sign in to Google again/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Add data' })).toBeInTheDocument()
  })

  it('shows Analysts a read-only list', async () => {
    routes()
    renderApp('/connections', { session: analyst })
    await screen.findByRole('article', { name: 'UrbanCart orders' })
    expect(screen.getByText(/An Owner adds and manages them/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Add data' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'Who sees which connections' }),
    ).not.toBeInTheDocument()
  })

  it('says what to do when there is nothing yet', async () => {
    routes({
      'GET /connections': { status: 200, body: { ...demoConnectionList, connections: [] } },
    })
    renderApp('/connections')
    expect(
      await screen.findByText('Connect your first data source to start asking questions.'),
    ).toBeInTheDocument()
  })

  it('shows that a source is being read', async () => {
    routes({
      'GET /connections': {
        status: 200,
        body: { ...demoConnectionList, connections: [{ ...demoOrders, sync_state: 'running' }] },
      },
    })
    renderApp('/connections')
    expect(await screen.findByText('Reading the data…')).toBeInTheDocument()
  })
})

describe('Uploading', () => {
  it('sends the file and says it is being read', async () => {
    const api = routes({ 'POST /connections/files': { status: 201, body: demoOrders } })
    renderApp('/connections')
    const input = await screen.findByTestId('file-input')
    const file = new File(['Date,Sales\n05/01/2026,100\n'], 'sales.csv', { type: 'text/csv' })
    await userEvent.upload(input, file)
    expect(await screen.findByText('sales.csv was uploaded. Reading it now.')).toBeInTheDocument()
    const call = api.calls.find((c) => c.key === 'POST /connections/files')
    expect(call?.init.body).toBeInstanceOf(FormData)
    expect((call?.init.body as FormData).get('file')).toBeInstanceOf(File)
    expect((call?.init.headers as Record<string, string>).Authorization).toMatch(/^Bearer /)
  })

  it('checks type and size before sending', async () => {
    const api = routes()
    renderApp('/connections')
    const input = await screen.findByTestId('file-input')
    fireEvent.change(input, {
      target: { files: [new File(['%PDF'], 'notes.pdf', { type: 'application/pdf' })] },
    })
    expect(
      await screen.findByText('Upload an Excel workbook (.xlsx) or a CSV file (.csv).'),
    ).toBeInTheDocument()
    const big = new File(['x'], 'big.csv')
    Object.defineProperty(big, 'size', { value: 51 * 1024 * 1024 })
    fireEvent.change(input, { target: { files: [big] } })
    expect(await screen.findByText(/larger than 50 MB/)).toBeInTheDocument()
    expect(api.count('POST /connections/files')).toBe(0)
  })

  it('shows why the server could not read a file', async () => {
    routes({
      'POST /connections/files': apiError(422, 'file_unreadable', 'Damaged.', {
        file: 'This workbook is damaged and cannot be opened.',
      }),
    })
    renderApp('/connections')
    const input = await screen.findByTestId('file-input')
    await userEvent.upload(input, new File(['PK'], 'plan.xlsx'))
    expect(
      await screen.findByText('This workbook is damaged and cannot be opened.'),
    ).toBeInTheDocument()
  })
})

describe('Google Sheets', () => {
  it('sends the Owner to Google', async () => {
    routes({
      'POST /connections/google/start': {
        status: 200,
        body: { authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?x=1' },
      },
    })
    renderApp('/connections')
    await userEvent.click(await screen.findByRole('button', { name: 'Connect Google Sheets' }))
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith('https://accounts.google.com/o/oauth2/v2/auth?x=1'),
    )
  })

  it('cannot be started when the server has no Google credentials', async () => {
    routes({
      'GET /connections': {
        status: 200,
        body: {
          ...demoConnectionList,
          sources: {
            upload_max_mb: 50,
            google_sheets: { available: false, stand_in: false },
            razorpay: { available: true, stand_in: false },
            shopify: { available: true, stand_in: false },
          },
        },
      },
    })
    renderApp('/connections')
    expect(await screen.findByRole('button', { name: 'Connect Google Sheets' })).toBeDisabled()
    expect(screen.getByText(/Google Sheets is not set up on this server yet/)).toBeInTheDocument()
  })

  it('finishes the sign-in, lets the Owner choose spreadsheets and opens the connection', async () => {
    const api = routes({
      'POST /connections/google/finish': {
        status: 200,
        body: {
          connection: { ...demoMarketing, id: 'c9' },
          picker: standInPicker,
          reconnected: false,
        },
      },
      'PUT /connections/c9/google/spreadsheets': { status: 200, body: demoMarketing },
      'GET /connections/c9': {
        status: 200,
        body: { ...demoMarketing, id: 'c9', datasets: [], versions: [] },
      },
    })
    renderApp('/connections/google/return#state=s123&code=4/abc')
    const dialog = await screen.findByRole('dialog', { name: 'Choose spreadsheets' })
    expect(api.body('POST /connections/google/finish')).toEqual({ state: 's123', code: '4/abc' })
    await userEvent.click(within(dialog).getByLabelText('UrbanCart marketing plan'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use these spreadsheets' }))
    await screen.findByRole('heading', { level: 1, name: demoMarketing.name })
    expect(api.body('PUT /connections/c9/google/spreadsheets')).toEqual({
      spreadsheet_ids: ['standin-urbancart-marketing'],
    })
  })

  it('explains a cancelled Google sign-in', async () => {
    const api = routes()
    renderApp('/connections/google/return#error=access_denied')
    expect(
      await screen.findByText('Google sign-in was cancelled. Nothing was connected.'),
    ).toBeInTheDocument()
    expect(api.count('POST /connections/google/finish')).toBe(0)
  })

  it('shows the server’s reason when the sign-in is refused', async () => {
    routes({
      'POST /connections/google/finish': apiError(
        422,
        'google_refused',
        'Google gave this app more access than it asks for. Connect again and allow only reading.',
      ),
    })
    renderApp('/connections/google/return#state=s1&code=4/x')
    expect(await screen.findByText(/more access than it asks for/)).toBeInTheDocument()
  })
})

describe('Who sees which connections', () => {
  it('lists people with what they see and changes it', async () => {
    const api = routes({
      'PUT /connections/access/m2': {
        status: 200,
        body: { ...demoAccess[1], access: 'all', connection_ids: [] },
      },
    })
    renderApp('/connections')
    const list = await screen.findByRole('list', { name: 'Who sees which connections' })
    expect(within(list).getByText('Owner: sees every connection')).toBeInTheDocument()
    expect(within(list).getByText('1 of 3 connections')).toBeInTheDocument()
    expect(within(list).getByText('All connections')).toBeInTheDocument()
    await userEvent.click(
      within(list).getByRole('button', { name: 'Change what Ravi Kumar can see' }),
    )
    const dialog = await screen.findByRole('dialog', { name: 'What Ravi Kumar can see' })
    expect(within(dialog).getByLabelText('UrbanCart orders')).toBeChecked()
    await userEvent.click(within(dialog).getByLabelText(/All connections, including/))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save access' }))
    expect(await screen.findByText('Ravi Kumar now sees all connections.')).toBeInTheDocument()
    expect(api.body('PUT /connections/access/m2')).toEqual({ access: 'all', connection_ids: [] })
  })

  it('can limit someone to no connections at all', async () => {
    const api = routes({
      'PUT /connections/access/m3': {
        status: 200,
        body: { ...demoAccess[2], access: 'selected', connection_ids: [] },
      },
    })
    renderApp('/connections')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Change what Lata Menon can see' }),
    )
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByLabelText('Only these connections'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save access' }))
    expect(await screen.findByText('Lata Menon now sees no connections.')).toBeInTheDocument()
    expect(api.body('PUT /connections/access/m3')).toEqual({
      access: 'selected',
      connection_ids: [],
    })
  })
})

describe('A connection', () => {
  it('shows its tables, data notes and file versions', async () => {
    routes()
    renderApp('/connections/c1')
    expect(await screen.findByRole('heading', { level: 1, name: 'UrbanCart orders' })).toBeVisible()
    expect(screen.getByText(/Excel or CSV file · Added by Asha Rao on 21 Oct 2026/)).toBeVisible()
    const tables = screen.getByRole('list', { name: 'Tables' })
    expect(within(tables).getByText('28,540 rows · 7 columns · Updated 30 Oct 2026')).toBeVisible()
    expect(
      within(tables).getByText(
        /Rows with sales_channel = Website look missing for the week of 9 Mar 2026: 0 rows, usually about 95\./,
      ),
    ).toBeVisible()
    expect(
      within(tables).getByText(
        /The newest row is from 30 Sept? 2026, 40 days ago\. Recent activity may be missing\./,
      ),
    ).toBeVisible()
    const versions = screen.getByRole('list', { name: 'File versions' })
    const rows = within(versions)
      .getAllByRole('listitem')
      .filter((li) => li.parentElement === versions)
    expect(rows).toHaveLength(3)
    expect(within(rows[1]!).getByText('In use')).toBeVisible()
  })

  it('shows an upload that waits for review, with what changed', async () => {
    routes()
    renderApp('/connections/c1')
    expect(
      await screen.findByText(/Version 3 was not used yet: it differs from the version in use/),
    ).toBeVisible()
    const versions = screen.getByRole('list', { name: 'File versions' })
    expect(within(versions).getByText('Needs your review')).toBeVisible()
    expect(
      within(versions).getByText(
        'shopify_orders: 2 columns are gone (sales_channel, shipping_region).',
      ),
    ).toBeVisible()
    // The version in use offers neither "Use" nor "Discard".
    expect(
      screen.queryByRole('button', { name: 'Use this version (version 2)' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Discard version 2' })).not.toBeInTheDocument()
  })

  it('uses a reviewed version, or switches back to an earlier one', async () => {
    const api = routes({
      'POST /connections/c1/versions/v3/use': { status: 200, body: demoOrders },
      'POST /connections/c1/versions/v1/use': { status: 200, body: demoOrders },
    })
    renderApp('/connections/c1')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Use this version (version 3)' }),
    )
    expect(await screen.findByText('Switching to version 3. Reading it now.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Use this version (version 1)' }))
    expect(await screen.findByText('Switching to version 1. Reading it now.')).toBeVisible()
    expect(api.count('POST /connections/c1/versions/v3/use')).toBe(1)
    expect(api.count('POST /connections/c1/versions/v1/use')).toBe(1)
  })

  it('discards a version after confirming', async () => {
    const api = routes({ 'DELETE /connections/c1/versions/v3': { status: 204 } })
    renderApp('/connections/c1')
    await userEvent.click(await screen.findByRole('button', { name: 'Discard version 3' }))
    const dialog = await screen.findByRole('dialog', { name: 'Discard version 3?' })
    expect(within(dialog).getByText(/The version in use does not change/)).toBeVisible()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Discard version 3' }))
    expect(await screen.findByText('Version 3 was discarded.')).toBeVisible()
    expect(api.count('DELETE /connections/c1/versions/v3')).toBe(1)
  })

  it('shows the server’s reason when a version cannot be used', async () => {
    routes({
      'POST /connections/c1/versions/v3/use': apiError(
        409,
        'already_reading',
        'This connection is being read already.',
      ),
    })
    renderApp('/connections/c1')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Use this version (version 3)' }),
    )
    expect(await screen.findByText('This connection is being read already.')).toBeVisible()
  })

  it('shows columns and the first rows on request', async () => {
    routes()
    renderApp('/connections/c1')
    await userEvent.click(await screen.findByRole('button', { name: 'Columns and preview' }))
    const table = await screen.findByRole('table', { name: /First 4 of 28,540 rows/ })
    const rows = within(table).getAllByRole('row')
    expect(within(rows[1]!).getByText('1 Jan 2025 00:25')).toBeVisible()
    expect(within(rows[1]!).getByText('5000000001')).toBeVisible() // an id, not a quantity
    expect(within(rows[1]!).getByText('644.86')).toBeVisible()
    expect(within(rows[4]!).getByText('—')).toBeVisible() // empty, not zero
    expect(screen.getByText('Date and time')).toBeVisible()
    // The file has no ₹ marker (only a currency column), so the reader gives total_price no unit; the
    // business dictionary supplies it (M7).
    expect(screen.queryByText('₹')).not.toBeInTheDocument()
  })

  it('refreshes, tests, renames and removes', async () => {
    const api = routes({
      'POST /connections/c1/refresh': {
        status: 200,
        body: { ...demoOrders, sync_state: 'queued' },
      },
      'POST /connections/c1/test': {
        status: 200,
        body: {
          ok: true,
          message: 'shopify_orders.csv (version 2) opens and has 1 sheet.',
          checks: [
            { label: 'file_readable', ok: true },
            { label: 'read_only', ok: true },
          ],
        },
      },
      'PATCH /connections/c1': { status: 200, body: { ...demoOrders, name: 'Orders' } },
      'DELETE /connections/c1': { status: 204 },
    })
    renderApp('/connections/c1')
    await userEvent.click(await screen.findByRole('button', { name: 'Refresh now' }))
    expect(await screen.findByText('Reading the data again now.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText(/opens and has 1 sheet/)).toBeVisible()
    expect(screen.getByText('✓ The file opens · ✓ Read-only access')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Rename' }))
    const rename = await screen.findByRole('dialog', { name: 'Rename UrbanCart orders' })
    const field = within(rename).getByLabelText('Name')
    await userEvent.clear(field)
    await userEvent.type(field, 'Orders')
    await userEvent.click(within(rename).getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Renamed to Orders.')).toBeVisible()
    expect(api.body('PATCH /connections/c1')).toEqual({ name: 'Orders' })

    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
    const confirm = await screen.findByRole('dialog', { name: 'Remove UrbanCart orders?' })
    await userEvent.click(within(confirm).getByRole('button', { name: 'Remove UrbanCart orders' }))
    expect(await screen.findByText('UrbanCart orders was removed.')).toBeVisible()
    expect(api.count('DELETE /connections/c1')).toBe(1)
  })

  it('warns before removal what happens to the business dictionary', async () => {
    routes({
      'GET /connections/c1': {
        status: 200,
        body: {
          ...demoOrdersDetail,
          dictionary_impact: {
            moves: [{ table: 'shopify_orders', to: 'Orders sheet', metrics: 4 }],
            orphaned_metrics: ['repeat_rate'],
            joins: 1,
          },
        },
      },
    })
    renderApp('/connections/c1')
    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }))
    const confirm = await screen.findByRole('dialog', { name: 'Remove UrbanCart orders?' })
    expect(
      within(confirm).getByText(
        'shopify_orders in Orders sheet has the same name and columns, so it takes over 4 metrics.',
      ),
    ).toBeVisible()
    expect(within(confirm).getByText(/The metric repeat_rate would have no table/)).toBeVisible()
    expect(within(confirm).getByText('1 join between tables goes with it.')).toBeVisible()
    // Fix 3: removing loses the Owner's edits; the dialog says so and points to a new version.
    expect(
      within(confirm).getByText(/uploading the same file again starts from a new guess/),
    ).toBeVisible()
    expect(within(confirm).getByText(/upload a new version of the file/)).toBeVisible()
  })

  it('offers to reconnect Google when the sign-in stopped working', async () => {
    routes({
      'GET /connections/c3': {
        status: 200,
        body: { ...demoTargets, datasets: [], versions: [] },
      },
      'POST /connections/google/start': {
        status: 200,
        body: { authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?hint=1' },
      },
    })
    renderApp('/connections/c3')
    await userEvent.click(await screen.findByRole('button', { name: 'Reconnect Google Sheets' }))
    await waitFor(() => expect(assign).toHaveBeenCalled())
  })

  it('adds spreadsheets with the Picker and keeps the ones already chosen', async () => {
    const api = routes({
      'GET /connections/c2': {
        status: 200,
        body: { ...demoMarketing, datasets: [], versions: [] },
      },
      'POST /connections/c2/google/picker-token': { status: 200, body: standInPicker },
      'PUT /connections/c2/google/spreadsheets': { status: 200, body: demoMarketing },
    })
    renderApp('/connections/c2')
    await userEvent.click(await screen.findByRole('button', { name: 'Choose spreadsheets' }))
    const dialog = await screen.findByRole('dialog', { name: 'Choose spreadsheets' })
    await userEvent.click(within(dialog).getByLabelText('Outlet sales (sample)'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use these spreadsheets' }))
    expect(await screen.findByText('Spreadsheets saved. Reading their tabs now.')).toBeVisible()
    expect(api.body('PUT /connections/c2/google/spreadsheets')).toEqual({
      spreadsheet_ids: ['sheet-marketing', 'standin-sample-outlets'],
    })
  })

  it('stops reading a spreadsheet', async () => {
    const api = routes({
      'GET /connections/c2': {
        status: 200,
        body: { ...demoMarketing, datasets: [], versions: [] },
      },
      'PUT /connections/c2/google/spreadsheets': { status: 200, body: demoMarketing },
    })
    renderApp('/connections/c2')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Stop reading UrbanCart marketing plan' }),
    )
    await waitFor(() =>
      expect(api.body('PUT /connections/c2/google/spreadsheets')).toEqual({ spreadsheet_ids: [] }),
    )
  })

  it('is read-only for Analysts', async () => {
    routes()
    renderApp('/connections/c1', { session: analyst })
    await screen.findByRole('heading', { level: 1, name: 'UrbanCart orders' })
    expect(screen.queryByRole('button', { name: 'Refresh now' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Upload a new version' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Columns and preview' })).toBeVisible()
  })

  it('says when a connection cannot be found', async () => {
    routes({ 'GET /connections/nope': apiError(404, 'not_found', 'Not found.') })
    renderApp('/connections/nope')
    expect(await screen.findByText('This connection could not be loaded.')).toBeVisible()
  })
})

describe('Razorpay', () => {
  const connected = { ...demoRazorpay, sync_state: 'queued' as const }

  async function open() {
    renderApp('/connections')
    await userEvent.click(await screen.findByRole('button', { name: 'Connect Razorpay' }))
    return screen.findByRole('dialog', { name: 'Connect Razorpay' })
  }

  it('says keys can make changes and that this app only reads', async () => {
    routes()
    const dialog = await open()
    expect(
      within(dialog).getByText(/Razorpay keys can make changes; this app only reads/),
    ).toBeVisible()
    expect(within(dialog).getByText(/Create a separate key/)).toBeVisible()
  })

  it('checks the key id and secret before sending anything', async () => {
    const api = routes()
    const dialog = await open()
    await userEvent.type(within(dialog).getByLabelText('Key id'), 'hello')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Check and connect' }))
    expect(within(dialog).getByText('The key id starts with rzp_live_ or rzp_test_.')).toBeVisible()
    expect(within(dialog).getByText('Enter the key secret (no spaces).')).toBeVisible()
    expect(api.count('POST /connections/razorpay')).toBe(0)
  })

  it('connects with a key, never shows the secret again and says it is reading', async () => {
    const api = routes({ 'POST /connections/razorpay': { status: 201, body: connected } })
    const dialog = await open()
    await userEvent.type(within(dialog).getByLabelText('Key id'), 'rzp_live_AbCdEf123456')
    await userEvent.type(within(dialog).getByLabelText('Key secret'), 's3cr3tK3yValue99')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Check and connect' }))
    expect(
      await screen.findByText('Razorpay is connected. Reading your payments now.'),
    ).toBeVisible()
    expect(api.body('POST /connections/razorpay')).toEqual({
      key_id: 'rzp_live_AbCdEf123456',
      key_secret: 's3cr3tK3yValue99',
    })
    expect(screen.queryByDisplayValue('s3cr3tK3yValue99')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows Razorpay’s refusal in the dialog and keeps it open', async () => {
    routes({
      'POST /connections/razorpay': apiError(
        422,
        'razorpay_refused',
        'Razorpay did not accept the API key. Enter the key id and secret again.',
      ),
    })
    const dialog = await open()
    await userEvent.type(within(dialog).getByLabelText('Key id'), 'rzp_live_AbCdEf123456')
    await userEvent.type(within(dialog).getByLabelText('Key secret'), 'wrongWrongWrong1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Check and connect' }))
    expect(await within(dialog).findByText(/did not accept the API key/)).toBeVisible()
  })

  it('is a card with the Razorpay label', async () => {
    routes({
      'GET /connections': {
        status: 200,
        body: { ...demoConnectionList, connections: [demoRazorpay] },
      },
    })
    renderApp('/connections')
    const card = await screen.findByRole('article', { name: 'Razorpay' })
    expect(within(card).getByText('4 tables · 18,203 rows')).toBeInTheDocument()
  })

  it('shows the masked key, the mode and the read-only note on the connection page', async () => {
    routes({
      'GET /connections/c4': {
        status: 200,
        body: { ...demoRazorpay, datasets: [], versions: [] },
      },
    })
    renderApp('/connections/c4')
    expect(await screen.findByText(/Razorpay key rzp_live_AbCd••••/)).toBeVisible()
    expect(screen.getByText(/Live mode/)).toBeVisible()
    expect(screen.getByText(/Amounts are kept in paise/)).toBeVisible()
  })

  it('offers a new key when Razorpay stopped accepting the old one, and saves it', async () => {
    const broken = {
      ...demoRazorpay,
      status: 'needs_reauth' as const,
      last_error: 'Razorpay did not accept the API key. Enter the key id and secret again.',
      datasets: [],
      versions: [],
    }
    const api = routes({
      'GET /connections/c4': { status: 200, body: broken },
      'PUT /connections/c4/razorpay/key': {
        status: 200,
        body: { ...demoRazorpay, sync_state: 'queued' },
      },
    })
    renderApp('/connections/c4')
    await userEvent.click(await screen.findByRole('button', { name: 'Enter a new key' }))
    const dialog = await screen.findByRole('dialog', { name: 'Enter a new Razorpay key' })
    expect(within(dialog).getByText('Current key: rzp_live_AbCd••••')).toBeVisible()
    await userEvent.type(within(dialog).getByLabelText('Key id'), 'rzp_live_NewKey123456')
    await userEvent.type(within(dialog).getByLabelText('Key secret'), 'newSecretValue12')
    await userEvent.click(
      within(dialog).getByRole('button', { name: 'Check and save the new key' }),
    )
    await waitFor(() => expect(api.count('PUT /connections/c4/razorpay/key')).toBe(1))
    expect(await screen.findByText(/The new key works/)).toBeVisible()
  })

  it('does not offer Analysts a way to change the key', async () => {
    routes({
      'GET /connections': {
        status: 200,
        body: { ...demoConnectionList, connections: [demoRazorpay] },
      },
      'GET /connections/c4': { status: 200, body: { ...demoRazorpay, datasets: [], versions: [] } },
    })
    renderApp('/connections/c4', { session: analyst })
    await screen.findByText(/Razorpay key rzp_live_AbCd••••/)
    expect(screen.queryByRole('button', { name: 'Replace the API key' })).not.toBeInTheDocument()
  })
})

describe('Shopify', () => {
  const open = async () => {
    renderApp('/connections')
    await userEvent.click(await screen.findByRole('button', { name: 'Connect Shopify' }))
    return screen.findByRole('dialog', { name: 'Connect Shopify' })
  }

  it('asks for the store address and checks it before going to Shopify', async () => {
    const api = routes()
    const dialog = await open()
    expect(within(dialog).getByText(/never changes anything in your store/)).toBeVisible()
    await userEvent.type(within(dialog).getByLabelText('Store address'), 'not a store!')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Continue to Shopify' }))
    expect(await within(dialog).findByText(/Enter the store address/)).toBeVisible()
    expect(api.count('POST /connections/shopify/start')).toBe(0)
  })

  it('sends the Owner to Shopify to approve read-only access', async () => {
    const url = 'https://urbancart.myshopify.com/admin/oauth/authorize?state=s'
    const api = routes({
      'POST /connections/shopify/start': { status: 200, body: { authorization_url: url } },
    })
    const dialog = await open()
    await userEvent.type(within(dialog).getByLabelText('Store address'), 'urbancart')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Continue to Shopify' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith(url))
    expect(api.body('POST /connections/shopify/start')).toEqual({ shop: 'urbancart' })
  })

  it('shows the server’s answer about the address in the field', async () => {
    routes({
      'POST /connections/shopify/start': apiError(
        422,
        'validation_error',
        'Check the store address.',
        {
          shop: 'Enter the store’s address, for example your-store.myshopify.com.',
        },
      ),
    })
    const dialog = await open()
    await userEvent.type(within(dialog).getByLabelText('Store address'), 'closed-store')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Continue to Shopify' }))
    expect(await within(dialog).findByText(/Enter the store’s address/)).toBeVisible()
    expect(assign).not.toHaveBeenCalled()
  })

  it('is offered as a stand-in in development and disabled without an app', async () => {
    routes({
      'GET /connections': {
        status: 200,
        body: {
          ...demoConnectionList,
          sources: {
            ...demoConnectionList.sources,
            shopify: { available: false, stand_in: false },
          },
        },
      },
    })
    renderApp('/connections')
    expect(await screen.findByRole('button', { name: 'Connect Shopify' })).toBeDisabled()
    expect(screen.getByText(/docs\/SETUP_SHOPIFY.md/)).toBeInTheDocument()
  })

  it('finishes the connection when Shopify sends the Owner back', async () => {
    const api = routes({
      'POST /connections/shopify/finish': {
        status: 200,
        body: { connection: { ...demoShopify, sync_state: 'queued' }, reconnected: false },
      },
      'GET /connections/c5': { status: 200, body: { ...demoShopify, datasets: [], versions: [] } },
    })
    renderApp('/connections/shopify/return#state=state-0123456789abcdef&code=abc')
    expect(await screen.findByRole('heading', { name: 'Shopify (UrbanCart)' })).toBeVisible()
    expect(api.body('POST /connections/shopify/finish')).toEqual({
      state: 'state-0123456789abcdef',
      code: 'abc',
    })
  })

  it('explains a cancelled approval and offers a way back', async () => {
    const api = routes()
    renderApp('/connections/shopify/return#error=access_denied')
    expect(await screen.findByText(/was not approved in Shopify/)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to connections' })).toBeVisible()
    expect(api.count('POST /connections/shopify/finish')).toBe(0)
  })

  it('shows the store, the 60-day limit and the read-only note on the connection page', async () => {
    routes({
      'GET /connections/c5': { status: 200, body: { ...demoShopify, datasets: [], versions: [] } },
    })
    renderApp('/connections/c5')
    expect(await screen.findByText(/Store urbancart.myshopify.com/)).toBeVisible()
    expect(screen.getByText(/last 60 days/)).toBeVisible()
    expect(screen.getByText(/Amounts are kept in rupees/)).toBeVisible()
  })

  it('offers to sign in to Shopify again when Shopify stopped sharing data', async () => {
    const url = 'https://urbancart.myshopify.com/admin/oauth/authorize?state=t'
    const api = routes({
      'GET /connections/c5': {
        status: 200,
        body: {
          ...demoShopify,
          status: 'needs_reauth',
          last_error: 'Shopify stopped sharing data. Sign in to Shopify again to reconnect.',
          datasets: [],
          versions: [],
        },
      },
      'POST /connections/shopify/start': { status: 200, body: { authorization_url: url } },
    })
    renderApp('/connections/c5')
    const alert = (await screen.findByText(/Shopify stopped sharing data/)).closest(
      '[role="alert"]',
    )
    const button = within(alert as HTMLElement).getByRole('button', {
      name: 'Sign in to Shopify again',
    })
    await userEvent.click(button)
    await waitFor(() => expect(assign).toHaveBeenCalledWith(url))
    expect(api.body('POST /connections/shopify/start')).toEqual({ connection_id: 'c5' })
  })

  it('does not offer Analysts a sign-in', async () => {
    routes({
      'GET /connections/c5': { status: 200, body: { ...demoShopify, datasets: [], versions: [] } },
    })
    renderApp('/connections/c5', { session: analyst })
    await screen.findByText(/Store urbancart.myshopify.com/)
    expect(
      screen.queryByRole('button', { name: 'Sign in to Shopify again' }),
    ).not.toBeInTheDocument()
  })
})

describe('Zoho Books', () => {
  const zohoDetail = (extra: object = {}) => ({
    status: 200,
    body: { ...demoZoho, datasets: [], versions: [], ...extra },
  })

  it('sends the Owner to Zoho to accept read-only access', async () => {
    const url = 'https://accounts.zoho.in/oauth/v2/auth?state=s'
    const api = routes({
      'POST /connections/zoho/start': { status: 200, body: { authorization_url: url } },
    })
    renderApp('/connections')
    await userEvent.click(await screen.findByRole('button', { name: 'Connect Zoho Books' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith(url))
    expect(api.body('POST /connections/zoho/start')).toEqual({})
  })

  it('is disabled without a Zoho client and says how to set it up', async () => {
    routes({
      'GET /connections': {
        status: 200,
        body: {
          ...demoConnectionList,
          sources: {
            ...demoConnectionList.sources,
            zoho_books: { available: false, stand_in: false, region: 'in' },
          },
        },
      },
    })
    renderApp('/connections')
    expect(await screen.findByRole('button', { name: 'Connect Zoho Books' })).toBeDisabled()
    expect(screen.getByText(/docs\/SETUP_ZOHO.md/)).toBeInTheDocument()
  })

  it('finishes the connection when Zoho sends the Owner back', async () => {
    const api = routes({
      'POST /connections/zoho/finish': {
        status: 200,
        body: { connection: { ...demoZoho, sync_state: 'queued' }, reconnected: false },
      },
      'GET /connections/c6': zohoDetail(),
    })
    renderApp('/connections/zoho/return#state=state-0123456789abcdef&code=abc')
    expect(await screen.findByRole('heading', { name: 'Zoho Books (UrbanCart)' })).toBeVisible()
    expect(api.body('POST /connections/zoho/finish')).toEqual({
      state: 'state-0123456789abcdef',
      code: 'abc',
    })
  })

  it('explains a refusal in Zoho and offers a way back', async () => {
    const api = routes()
    renderApp('/connections/zoho/return#error=access_denied')
    expect(await screen.findByText(/Access was not accepted in Zoho/)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to connections' })).toBeVisible()
    expect(api.count('POST /connections/zoho/finish')).toBe(0)
  })

  it('shows the organisation, data centre, API calls and the read-only note', async () => {
    routes({ 'GET /connections/c6': zohoDetail() })
    renderApp('/connections/c6')
    expect(await screen.findByText(/Organisation UrbanCart/)).toBeVisible()
    expect(screen.getByText(/Zoho data centre: India/)).toBeVisible()
    expect(screen.getByText(/Zoho API calls today: 57 of 1,000/)).toBeVisible()
    expect(screen.getByText(/phone numbers and addresses are never read/)).toBeVisible()
  })

  it('lets the Owner read another organisation', async () => {
    const api = routes({
      'GET /connections/c6': zohoDetail(),
      'PUT /connections/c6/zoho/organization': {
        status: 200,
        body: {
          ...demoZoho,
          zoho: { ...demoZoho.zoho!, organization_name: 'UrbanCart Labs', organizations: [] },
        },
      },
    })
    renderApp('/connections/c6')
    expect(await screen.findByLabelText('Read another organisation')).toHaveValue('60010000002')
    await userEvent.click(screen.getByRole('button', { name: 'Read this organisation' }))
    expect(await screen.findByText(/Now reading UrbanCart Labs/)).toBeVisible()
    expect(api.body('PUT /connections/c6/zoho/organization')).toEqual({
      organization_id: '60010000002',
    })
  })

  it('offers to sign in to Zoho again when Zoho stopped sharing data', async () => {
    const url = 'https://accounts.zoho.in/oauth/v2/auth?state=t'
    const api = routes({
      'GET /connections/c6': zohoDetail({
        status: 'needs_reauth',
        last_error: 'Zoho Books stopped sharing data. Sign in to Zoho again to reconnect.',
      }),
      'POST /connections/zoho/start': { status: 200, body: { authorization_url: url } },
    })
    renderApp('/connections/c6')
    const alert = (await screen.findByText(/Zoho Books stopped sharing data/)).closest(
      '[role="alert"]',
    )
    await userEvent.click(
      within(alert as HTMLElement).getByRole('button', { name: 'Sign in to Zoho again' }),
    )
    await waitFor(() => expect(assign).toHaveBeenCalledWith(url))
    expect(api.body('POST /connections/zoho/start')).toEqual({ connection_id: 'c6' })
  })

  it('shows analysts the organisation without the controls', async () => {
    routes({ 'GET /connections/c6': zohoDetail() })
    renderApp('/connections/c6', { session: analyst })
    expect(await screen.findByText(/Organisation UrbanCart/)).toBeVisible()
    expect(screen.queryByLabelText('Read another organisation')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Sign in to Zoho again' })).toBeNull()
  })
})

describe('Databases', () => {
  const open = async () => {
    renderApp('/connections')
    await userEvent.click(await screen.findByRole('button', { name: 'Connect a database' }))
    return screen.findByRole('dialog', { name: 'Connect a database' })
  }

  it('asks for the details, checks them and opens the new connection', async () => {
    const api = routes({
      'POST /connections/database': {
        status: 201,
        body: { ...demoDatabase, database: { ...demoDatabase.database!, tables: [] } },
      },
      'GET /connections/c7': {
        status: 200,
        body: {
          ...demoDatabaseDetail,
          datasets: [],
          database: { ...demoDatabase.database!, tables: [] },
        },
      },
      'GET /connections/c7/database/tables': { status: 200, body: demoDatabaseTables },
    })
    const dialog = await open()
    expect(within(dialog).getByText(/Create a read-only user/)).toBeVisible()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Check and connect' }))
    expect(within(dialog).getAllByText('Fill this in.')).toHaveLength(4)
    expect(api.count('POST /connections/database')).toBe(0)
    await userEvent.type(within(dialog).getByLabelText('Host'), 'db.urbancart.in')
    await userEvent.type(within(dialog).getByLabelText('Database name'), 'urbancart_demo')
    await userEvent.type(within(dialog).getByLabelText('User name'), 'analyst_ro')
    await userEvent.type(within(dialog).getByLabelText('Password'), 'secret-pass')
    expect(within(dialog).queryByRole('option', { name: /Not encrypted/ })).toBeNull()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Check and connect' }))
    expect(await screen.findByText(/No tables are chosen yet/)).toBeVisible()
    expect(api.body('POST /connections/database')).toEqual({
      engine: 'postgresql',
      host: 'db.urbancart.in',
      database: 'urbancart_demo',
      username: 'analyst_ro',
      password: 'secret-pass',
      ssl_mode: 'require',
    })
  })

  it('shows the server’s reason when the database is refused', async () => {
    routes({
      'POST /connections/database': apiError(
        422,
        'database_refused',
        'This address is private or internal (for example localhost, 10.x, 192.168.x or 169.254.x).',
      ),
    })
    const dialog = await open()
    await userEvent.type(within(dialog).getByLabelText('Host'), 'localhost')
    await userEvent.type(within(dialog).getByLabelText('Database name'), 'shop')
    await userEvent.type(within(dialog).getByLabelText('User name'), 'u')
    await userEvent.type(within(dialog).getByLabelText('Password'), 'p')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Check and connect' }))
    expect(await within(dialog).findByText(/private or internal/)).toBeVisible()
  })

  it('offers unencrypted connections and the demo details only in development', async () => {
    routes({
      'GET /connections': {
        status: 200,
        body: {
          ...demoConnectionList,
          sources: {
            ...demoConnectionList.sources,
            database: {
              available: true,
              ssl_off_allowed: true,
              demo_hosts: ['demo-mysql', 'demo-postgres'],
            },
          },
        },
      },
    })
    const dialog = await open()
    expect(within(dialog).getByRole('option', { name: /Not encrypted/ })).toBeInTheDocument()
    expect(within(dialog).getByText(/demo-mysql or demo-postgres/)).toBeVisible()
  })

  it('shows where the database is, the write warning and lets the Owner choose tables', async () => {
    const api = routes({
      'GET /connections/c7': { status: 200, body: demoDatabaseDetail },
      'GET /connections/c7/database/tables': { status: 200, body: demoDatabaseTables },
      'PUT /connections/c7/database/tables': { status: 200, body: demoDatabase },
    })
    renderApp('/connections/c7')
    expect(await screen.findByText(/PostgreSQL at db.urbancart.in:5432/)).toBeVisible()
    expect(screen.getByText(/user an••••/)).toBeVisible()
    expect(screen.getByText(/This database user can change data/)).toBeVisible()
    const list = await screen.findByRole('list', { name: 'Tables in the database' })
    expect(within(list).getByLabelText('orders')).toBeChecked()
    expect(within(list).getByLabelText('monthly_sales')).not.toBeChecked()
    await userEvent.click(within(list).getByLabelText('monthly_sales'))
    await userEvent.click(screen.getByRole('button', { name: 'Save tables' }))
    expect(await screen.findByText(/Reading the chosen tables now/)).toBeVisible()
    expect(api.body('PUT /connections/c7/database/tables')).toEqual({
      tables: ['public.orders', 'public.customers', 'public.monthly_sales'],
    })
  })

  it('asks for the credentials again when the database refuses them', async () => {
    const api = routes({
      'GET /connections/c7': {
        status: 200,
        body: {
          ...demoDatabaseDetail,
          status: 'needs_reauth',
          last_error:
            'The database did not accept the user name or password. Enter the credentials again.',
        },
      },
      'GET /connections/c7/database/tables': { status: 200, body: demoDatabaseTables },
      'PUT /connections/c7/database/credentials': { status: 200, body: demoDatabase },
    })
    renderApp('/connections/c7')
    const alert = (await screen.findByText(/did not accept the user name/)).closest(
      '[role="alert"]',
    )
    await userEvent.click(
      within(alert as HTMLElement).getByRole('button', { name: 'Enter the credentials again' }),
    )
    const dialog = await screen.findByRole('dialog', { name: 'Replace the database credentials' })
    expect(within(dialog).getByLabelText('Host')).toHaveValue('db.urbancart.in')
    expect(within(dialog).getByLabelText('Database')).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('User name'), 'analyst_ro')
    await userEvent.type(within(dialog).getByLabelText('Password'), 'new-pass')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByText(/The new credentials work/)).toBeVisible()
    expect(api.body('PUT /connections/c7/database/credentials')).toMatchObject({
      engine: 'postgresql',
      host: 'db.urbancart.in',
      port: 5432,
      ssl_mode: 'verify-full',
      password: 'new-pass',
    })
  })

  it('shows analysts the database without the controls', async () => {
    routes({ 'GET /connections/c7': { status: 200, body: demoDatabaseDetail } })
    renderApp('/connections/c7', { session: analyst })
    expect(await screen.findByText(/PostgreSQL at db.urbancart.in/)).toBeVisible()
    expect(screen.queryByRole('list', { name: 'Tables in the database' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Replace credentials' })).toBeNull()
  })
})
