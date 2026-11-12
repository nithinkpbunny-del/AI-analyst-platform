import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DictionaryContent } from '../../lib/dictionaryApi'
import { demoSession } from '../../lib/demoSession'
import { ROLE_PERMISSIONS } from '../../lib/permissions'
import { renderApp } from '../../test-utils'
import {
  checkedDictionaryContent,
  confirmedState,
  confirmedWithDraftState,
  firstDraftState,
  version,
} from '../../testing/dictionaryFixtures'
import {
  demoConnectionList,
  demoOrdersDetail,
  demoDatasetPreview,
  demoAccess,
} from '../../testing/connectionFixtures'
import { apiError, fakeApi } from '../../testing/fakeApi'

const analyst = { ...demoSession, role: 'analyst' as const, permissions: ROLE_PERMISSIONS.analyst }

afterEach(() => vi.unstubAllGlobals())

describe('Dictionary: reviewing the first draft', () => {
  it('explains what was found, flags what needs a check, and holds back the confirm button', async () => {
    fakeApi({ 'GET /dictionary': { status: 200, body: firstDraftState } })
    renderApp('/dictionary')
    expect(await screen.findByText('We looked at 2 tables and 7 columns.')).toBeInTheDocument()
    expect(screen.getByText('3 items need your check before you can confirm.')).toBeInTheDocument()
    expect(screen.getByText('Not confirmed yet')).toBeInTheDocument()
    expect(screen.getByText(/every answer says "dictionary not confirmed"/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm dictionary' })).toBeDisabled()
    // The broken metric is shown with its reason, the AI note says no rows were sent.
    expect(
      screen.getByText('A column in the formula does not exist in this table.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/Your rows were never sent/)).toBeInTheDocument()
  })

  it('shows rupee columns with the evidence and paise columns flagged with why', async () => {
    fakeApi({ 'GET /dictionary': { status: 200, body: firstDraftState } })
    renderApp('/dictionary')
    await screen.findByText('We looked at 2 tables and 7 columns.')
    const orders = document.querySelector('[data-table="shopify_orders"]') as HTMLElement
    await userEvent.click(within(orders).getByText('shopify_orders'))
    expect(within(orders).getByLabelText('Unit of total_price')).toHaveValue('INR')
    expect(
      within(orders).getByText(
        'Because of: a money-like name, a currency column that only says INR',
      ),
    ).toBeInTheDocument()
    expect(within(orders).getByLabelText('total_price is stored in paise')).not.toBeChecked()
    const payments = document.querySelector('[data-table="razorpay_payments"]') as HTMLElement
    expect(within(payments).getByLabelText('amount is stored in paise')).toBeChecked()
    expect(within(payments).getAllByText(/look like paise/)).toHaveLength(2)
    expect(within(payments).getByText('2 to check')).toBeInTheDocument()
  })

  it('lets the Owner check each flagged item, then saves and confirms', async () => {
    const calls = fakeApi({
      'GET /dictionary': { status: 200, body: firstDraftState },
      'PUT /dictionary/draft': (init) => ({
        status: 200,
        body: {
          ...version('draft', JSON.parse(String(init.body)) as DictionaryContent),
          review_items: 0,
        },
      }),
      'POST /dictionary/draft/confirm': {
        status: 200,
        body: version('confirmed', checkedDictionaryContent()),
      },
    })
    renderApp('/dictionary')
    await screen.findByText('We looked at 2 tables and 7 columns.')
    // Remove the broken metric, say amounts are not paise, and check the fee.
    await userEvent.click(screen.getByRole('button', { name: 'Remove margin' }))
    await userEvent.click(screen.getByLabelText('amount is stored in paise'))
    await userEvent.click(screen.getByRole('button', { name: 'Looks right: amount' }))
    await userEvent.click(screen.getByRole('button', { name: 'Looks right: fee' }))
    expect(
      screen.getByText('Everything is checked. Confirm when you are ready.'),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm dictionary' }))
    expect(await screen.findByText('Confirmed. Answers now use version 1.')).toBeInTheDocument()
    const saved = calls.body('PUT /dictionary/draft') as DictionaryContent
    expect(saved.datasets.razorpay_payments!.columns.amount!.divide_by).toBeNull()
    expect(saved.datasets.razorpay_payments!.columns.amount!.needs_review).toBe(false)
    expect(saved.datasets.razorpay_payments!.columns.fee!.divide_by).toBe(100)
    expect(saved.metrics.margin).toBeUndefined()
    expect(calls.count('POST /dictionary/draft/confirm')).toBe(1)
    expect(calls.calls.findIndex((c) => c.key === 'PUT /dictionary/draft')).toBeLessThan(
      calls.calls.findIndex((c) => c.key === 'POST /dictionary/draft/confirm'),
    )
  })

  it('edits meanings, roles and units and saves them', async () => {
    const calls = fakeApi({
      'GET /dictionary': { status: 200, body: firstDraftState },
      'PUT /dictionary/draft': { status: 200, body: firstDraftState.draft },
    })
    renderApp('/dictionary')
    await screen.findByText('We looked at 2 tables and 7 columns.')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    const meaning = screen.getByLabelText('Meaning of total_price')
    await userEvent.clear(meaning)
    await userEvent.type(meaning, 'Order value')
    await userEvent.selectOptions(screen.getByLabelText('Role of total_price'), 'dimension')
    await userEvent.clear(screen.getByLabelText('Other words for total_price'))
    await userEvent.type(screen.getByLabelText('Other words for total_price'), 'gmv, turnover ,')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(calls.count('PUT /dictionary/draft')).toBe(1))
    const price = (calls.body('PUT /dictionary/draft') as DictionaryContent).datasets
      .shopify_orders!.columns.total_price!
    expect(price.meaning).toBe('Order value')
    expect(price.role).toBe('dimension')
    expect(price.synonyms).toEqual(['gmv', 'turnover'])
  })

  it('removes joins and adds a metric with a checked name', async () => {
    const calls = fakeApi({
      'GET /dictionary': { status: 200, body: firstDraftState },
      'PUT /dictionary/draft': { status: 200, body: firstDraftState.draft },
    })
    renderApp('/dictionary')
    await screen.findByText('We looked at 2 tables and 7 columns.')
    expect(screen.getByText('100% of values match', { exact: false })).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('button', {
        name: 'Remove join shopify_orders.customer_id to shopify_customers.id',
      }),
    )
    expect(screen.getByText('No joins found.')).toBeInTheDocument()
    const name = screen.getByLabelText('Metric name')
    await userEvent.type(name, 'revenue')
    expect(screen.getByText('There is a metric with this name already.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add metric' })).toBeDisabled()
    await userEvent.clear(name)
    await userEvent.type(name, 'orders')
    await userEvent.type(screen.getByLabelText(/^Formula \(SQL total/), 'COUNT(*)')
    await userEvent.click(screen.getByRole('button', { name: 'Add metric' }))
    expect(screen.getByText('Not tested yet')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(calls.count('PUT /dictionary/draft')).toBe(1))
    const saved = calls.body('PUT /dictionary/draft') as DictionaryContent
    expect(saved.relationships).toEqual([])
    expect(saved.metrics.orders).toMatchObject({ expr: 'COUNT(*)', dataset: 'shopify_orders' })
  })

  it("shows the server's reason when confirming is refused", async () => {
    fakeApi({
      'GET /dictionary': {
        status: 200,
        body: { ...firstDraftState, draft: version('draft', checkedDictionaryContent()) },
      },
      'POST /dictionary/draft/confirm': apiError(409, 'review_pending', 'Some items...'),
    })
    renderApp('/dictionary')
    await screen.findByText('We looked at 2 tables and 7 columns.')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm dictionary' }))
    expect(await screen.findByText(/Some items still need your check/)).toBeInTheDocument()
  })

  it('discards a draft after asking', async () => {
    const calls = fakeApi({
      'GET /dictionary': { status: 200, body: firstDraftState },
      'DELETE /dictionary/draft': { status: 204 },
    })
    renderApp('/dictionary')
    await screen.findByText('We looked at 2 tables and 7 columns.')
    await userEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
    expect(screen.getByText('Discard this draft?')).toBeInTheDocument()
    expect(calls.count('DELETE /dictionary/draft')).toBe(0)
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
    await waitFor(() => expect(calls.count('DELETE /dictionary/draft')).toBe(1))
  })

  it.each([
    ['unavailable:ai_key_missing', /AI is not set up for this workspace/],
    ['failed:quota', /quota is used up/],
    ['unreadable', /could not be used/],
  ])('says why the AI part is missing (%s)', async (status, pattern) => {
    fakeApi({
      'GET /dictionary': {
        status: 200,
        body: {
          ...firstDraftState,
          draft: { ...firstDraftState.draft, notes: { ai: status } },
        },
      },
    })
    renderApp('/dictionary')
    expect(await screen.findByText(pattern)).toBeInTheDocument()
  })

  it('says what is being drafted and offers nothing to confirm yet', async () => {
    fakeApi({
      'GET /dictionary': {
        status: 200,
        body: { confirmed: false, drafting: true, active: null, draft: null, versions: [] },
      },
    })
    renderApp('/dictionary')
    expect(await screen.findByText(/drafting the dictionary/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm dictionary' })).not.toBeInTheDocument()
  })

  it('points at Connections when there is nothing yet', async () => {
    fakeApi({
      'GET /dictionary': {
        status: 200,
        body: { confirmed: false, drafting: false, active: null, draft: null, versions: [] },
      },
    })
    renderApp('/dictionary')
    expect(
      await screen.findByText('No dictionary yet. Add a data source and a draft appears here.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Connections' })).toHaveAttribute(
      'href',
      '/connections',
    )
  })
})

describe('Dictionary: after confirming', () => {
  it('shows the confirmed version, its history and the way to a new draft', async () => {
    const calls = fakeApi({
      'GET /dictionary': { status: 200, body: confirmedState },
      'POST /dictionary/draft': { status: 202, body: { drafting: true } },
    })
    renderApp('/dictionary')
    expect(
      await screen.findByText('Version 1, confirmed by Asha Rao on 10 Nov 2026.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('Not confirmed yet')).not.toBeInTheDocument()
    expect(screen.getByText('Version history')).toBeInTheDocument()
    expect(
      screen.getByText('Confirmed 10 Nov 2026 by Asha Rao', { exact: false }),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Draft again' }))
    await waitFor(() => expect(calls.count('POST /dictionary/draft')).toBe(1))
    expect(calls.body('POST /dictionary/draft')).toEqual({ fresh_ai: false })
  })

  it('a schema-change draft says what changed and that answers still use the old version', async () => {
    fakeApi({ 'GET /dictionary': { status: 200, body: confirmedWithDraftState } })
    renderApp('/dictionary')
    expect(
      await screen.findByText('Answers keep using version 1 until you confirm the draft.'),
    ).toBeInTheDocument()
    expect(screen.getByText('What changed')).toBeInTheDocument()
    expect(screen.getByText('New column coupon in shopify_orders')).toBeInTheDocument()
    expect(screen.getByText('Column sales_channel is gone from shopify_orders')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm dictionary' })).toBeEnabled()
  })
})

describe('Dictionary for Analysts', () => {
  it('is read-only and shows the confirmed version', async () => {
    fakeApi({ 'GET /dictionary': { status: 200, body: { ...confirmedState, versions: [] } } })
    renderApp('/dictionary', { session: analyst })
    expect(
      await screen.findByText(
        'What the columns and numbers in your data sources mean. An Owner reviews it.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm dictionary' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Draft again' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Meaning of total_price')).not.toBeInTheDocument()
    expect(screen.queryByText('Version history')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download YAML' })).toBeInTheDocument()
  })

  it('says answers are unconfirmed while nothing is confirmed', async () => {
    fakeApi({
      'GET /dictionary': {
        status: 200,
        body: {
          confirmed: false,
          drafting: false,
          active: version('draft', checkedDictionaryContent()),
          draft: null,
          versions: [],
        },
      },
    })
    renderApp('/dictionary', { session: analyst })
    expect(await screen.findByText('Not confirmed yet')).toBeInTheDocument()
  })

  it('has the Dictionary in the menu', async () => {
    fakeApi({ 'GET /dictionary': { status: 200, body: confirmedState } })
    renderApp('/dictionary', { session: analyst })
    await screen.findByText('Version history', {}, { timeout: 100 }).catch(() => undefined)
    expect(screen.getAllByRole('link', { name: 'Dictionary' }).length).toBeGreaterThan(0)
  })
})

describe('Draft notice on Connections', () => {
  it('tells an Owner that a draft is waiting', async () => {
    fakeApi({
      'GET /connections': { status: 200, body: demoConnectionList },
      'GET /connections/access': { status: 200, body: { people: demoAccess } },
      'GET /dictionary': { status: 200, body: firstDraftState },
    })
    renderApp('/connections')
    expect(
      await screen.findByText('A dictionary draft is waiting for your review.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review it' })).toHaveAttribute('href', '/dictionary')
  })

  it('says nothing when there is no draft', async () => {
    fakeApi({
      'GET /connections': { status: 200, body: demoConnectionList },
      'GET /connections/access': { status: 200, body: { people: demoAccess } },
      'GET /dictionary': { status: 200, body: confirmedState },
    })
    renderApp('/connections')
    await screen.findByRole('article', { name: 'UrbanCart orders' })
    expect(
      screen.queryByText('A dictionary draft is waiting for your review.'),
    ).not.toBeInTheDocument()
  })
})

describe('Replacing a connection (R2)', () => {
  function routes(extra: Parameters<typeof fakeApi>[0] = {}) {
    return fakeApi({
      'GET /connections': { status: 200, body: demoConnectionList },
      'GET /connections/access': { status: 200, body: { people: demoAccess } },
      'GET /connections/c1': { status: 200, body: demoOrdersDetail },
      'GET /connections/c1/datasets/d1/preview': { status: 200, body: demoDatasetPreview },
      'GET /dictionary': { status: 200, body: confirmedState },
      ...extra,
    })
  }

  it('moves the dictionary to the chosen connection and opens the draft', async () => {
    const calls = routes({
      'GET /dictionary': { status: 200, body: confirmedWithDraftState },
      'POST /dictionary/replace': {
        status: 200,
        body: {
          matched: [['shopify_orders', 'Orders']],
          unmatched: [],
          removed_old: true,
          draft: confirmedWithDraftState.draft,
        },
      },
    })
    renderApp('/connections/c1')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Replace with another source' }),
    )
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/matched to this one/)).toBeInTheDocument()
    const choose = within(dialog).getByLabelText('New source')
    // Only other connections that were read can take over.
    const others = demoConnectionList.connections.filter(
      (c) => c.id !== 'c1' && c.datasets_count > 0,
    )
    expect(within(choose).getAllByRole('option')).toHaveLength(others.length + 1)
    expect(within(dialog).getByRole('button', { name: 'Move the dictionary' })).toBeDisabled()
    await userEvent.selectOptions(choose, others[0]!.id)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move the dictionary' }))
    await waitFor(() => expect(calls.count('POST /dictionary/replace')).toBe(1))
    expect(calls.body('POST /dictionary/replace')).toEqual({
      from_connection_id: 'c1',
      to_connection_id: others[0]!.id,
      remove_old: true,
    })
    expect(await screen.findByText('A draft is waiting for your review')).toBeInTheDocument()
  })

  it('can keep the old connection, and shows why a move was refused', async () => {
    const calls = routes({
      'POST /dictionary/replace': apiError(409, 'not_read', 'not read'),
    })
    renderApp('/connections/c1')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Replace with another source' }),
    )
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('checkbox'))
    expect(within(dialog).getByText(/will be drafted as new tables later/)).toBeInTheDocument()
    const others = demoConnectionList.connections.filter(
      (c) => c.id !== 'c1' && c.datasets_count > 0,
    )
    await userEvent.selectOptions(within(dialog).getByLabelText('New source'), others[0]!.id)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move the dictionary' }))
    expect(await within(dialog).findByText(/has not been read yet/)).toBeInTheDocument()
    expect((calls.body('POST /dictionary/replace') as { remove_old: boolean }).remove_old).toBe(
      false,
    )
  })
})
