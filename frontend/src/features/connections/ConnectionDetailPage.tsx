import './strings'
import '../dictionary/strings'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ChevronDown, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { lazy, Suspense, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { QualityBanner } from '../../components/QualityBanner'
import { Button } from '../../components/ui/button'
import { ConfirmDialog, Dialog, DialogClose, DialogContent } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { useSession } from '../../lib/authContext'
import { cn } from '../../lib/cn'
import {
  connectionsApi,
  type DictionaryImpact,
  isSyncing,
  uploadFile,
  type ConnectionDetail,
  type Dataset,
  type FileVersion,
  type TestResult,
} from '../../lib/connectionsApi'
import { formatDate, formatNumber, formatRelativeTime } from '../../lib/format'
import { ReplaceDialog } from '../dictionary/ReplaceDialog'
import { FileDrop } from './AddData'
import { CONNECTIONS, fileProblem } from './keys'
import { useSpreadsheetPicker } from './useSpreadsheetPicker'
import { ZohoLines, ZohoOrganizationPicker } from './ZohoDetails'
import { DatabaseLines, DatabaseTables } from './DatabaseDetails'
import { TallyLines, UnpairTally } from './TallyDetails'

const RazorpayDialog = lazy(() =>
  import('./RazorpayDialog').then((m) => ({ default: m.RazorpayDialog })),
)
const TallyDialog = lazy(() => import('./TallyDialog').then((m) => ({ default: m.TallyDialog })))
const DatabaseDialog = lazy(() =>
  import('./DatabaseDialog').then((m) => ({ default: m.DatabaseDialog })),
)
import {
  cellText,
  changeText,
  columnType,
  formatBytes,
  qualityText,
  typeLabel,
  unitLabel,
} from './text'

const POLL_MS = 2000
type Notice = { tone: 'success' | 'error'; text: string } | null

function message(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.fields.file ?? error.message
  return fallback
}

function Section({
  id,
  title,
  intro,
  children,
}: {
  id: string
  title: string
  intro?: string
  children: ReactNode
}) {
  return (
    <section
      aria-labelledby={id}
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id={id} className="text-h4">
        {title}
      </h2>
      {intro ? <p className="m-0 mt-2 max-w-prose text-small text-ink-muted">{intro}</p> : null}
      {children}
    </section>
  )
}

const statusStyle = {
  active: 'bg-gain-soft text-gain',
  needs_reauth: 'bg-caution-soft text-caution',
  error: 'bg-loss-soft text-loss',
  disabled: 'bg-paper text-ink-muted',
} as const

// ------------------------------------------------------------------------------------------------ tables
function Preview({ connectionId, dataset }: { connectionId: string; dataset: Dataset }) {
  const { t } = useTranslation()
  const preview = useQuery({
    queryKey: ['connections', connectionId, 'preview', dataset.id],
    queryFn: () => connectionsApi.preview(connectionId, dataset.id),
    staleTime: 60_000,
    retry: false,
  })
  if (preview.isPending) {
    return (
      <p role="status" className="m-0 mt-3 text-small text-ink-muted">
        {t('connections.detail.previewLoading')}
      </p>
    )
  }
  if (preview.isError) {
    return (
      <FormAlert tone="error" className="mt-3">
        {message(preview.error, t('connections.detail.previewFailed'))}
      </FormAlert>
    )
  }
  const { columns, rows, row_count, checked_at } = preview.data
  const captionId = `preview-${dataset.id}`
  return (
    <>
      <p id={captionId} className="m-0 mt-3 text-caption text-ink-muted">
        {t('connections.detail.previewCaption', {
          shown: rows.length,
          total: formatNumber(row_count),
          name: dataset.name,
          when: formatRelativeTime(checked_at),
        })}
      </p>
      {/* The table scrolls inside its own box, so the page never scrolls sideways on a phone. */}
      <div
        role="region"
        aria-labelledby={captionId}
        className="mt-2 max-w-full overflow-x-auto rounded-sheet border border-line"
        tabIndex={0}
      >
        <table
          aria-labelledby={captionId}
          className="w-max min-w-full border-collapse text-small tabular"
        >
          <thead>
            <tr className="border-b border-line">
              {columns.map((column) => (
                <th
                  key={column.name}
                  scope="col"
                  className="px-3 py-2 text-left font-medium whitespace-nowrap text-ink-muted"
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-b border-line last:border-b-0">
                {row.map((value, cell) => {
                  const column = columns[cell]
                  const numeric = column?.type === 'integer' || column?.type === 'number'
                  return (
                    <td
                      key={cell}
                      className={cn('px-3 py-2 whitespace-nowrap', numeric && 'text-right')}
                    >
                      {cellText(t, value, column)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function DatasetRow({ connectionId, dataset }: { connectionId: string; dataset: Dataset }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const warnings = dataset.quality.filter((q) => q.severity === 'warning')
  const panelId = `dataset-${dataset.id}`
  return (
    <li className="border-b border-line py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h3 className="m-0 font-sans text-body font-semibold tracking-normal break-words">
            {dataset.name}
          </h3>
          <p className="m-0 text-caption text-ink-muted tabular">
            {dataset.row_count !== null
              ? t('connections.detail.rows', { rows: formatNumber(dataset.row_count) })
              : t('connections.notFetched')}
            {' · '}
            {t('connections.detail.columns', { count: dataset.columns.length })}
            {dataset.last_fetched_at
              ? ` · ${t('connections.updated', { when: formatRelativeTime(dataset.last_fetched_at) })}`
              : ''}
          </p>
          {warnings.length ? (
            <p className="m-0 mt-1 flex items-center gap-1.5 text-caption text-ink">
              <TriangleAlert className="size-3.5 text-caution" aria-hidden />
              {t('connections.notes', { count: warnings.length })}
            </p>
          ) : null}
        </div>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronDown
            aria-hidden
            className={cn('transition-transform duration-150', open && 'rotate-180')}
          />
          {open ? t('connections.detail.hideDetails') : t('connections.detail.showDetails')}
        </Button>
      </div>
      {dataset.last_error ? (
        <FormAlert tone="error" className="mt-3">
          {dataset.last_error}
        </FormAlert>
      ) : null}
      {dataset.quality.length ? (
        <div className="mt-3 flex flex-col gap-2">
          {dataset.quality.map((issue, index) =>
            issue.severity === 'warning' ? (
              <QualityBanner key={index}>{qualityText(t, issue)}</QualityBanner>
            ) : (
              <p key={index} className="m-0 text-caption text-ink-muted">
                {qualityText(t, issue)}
              </p>
            ),
          )}
        </div>
      ) : null}
      {open ? (
        <div id={panelId} className="mt-3">
          <h4 className="m-0 font-sans text-small font-semibold tracking-normal">
            {t('connections.detail.columnsTitle')}
          </h4>
          <div className="mt-2 max-w-full overflow-x-auto">
            <table className="w-full border-collapse text-small">
              <thead>
                <tr className="border-b border-line text-left text-caption text-ink-muted">
                  <th scope="col" className="py-2 pr-4 font-medium">
                    {t('connections.detail.columnHeaders.column')}
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    {t('connections.detail.columnHeaders.type')}
                  </th>
                  <th scope="col" className="py-2 font-medium">
                    {t('connections.detail.columnHeaders.unit')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {dataset.columns.map((column) => (
                  <tr key={column.name} className="border-b border-line last:border-b-0">
                    <td className="py-2 pr-4 text-ink">{column.label}</td>
                    <td className="py-2 pr-4 text-ink-muted">{columnType(t, column)}</td>
                    <td className="py-2 text-ink-muted">{unitLabel(t, column.unit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h4 className="m-0 mt-4 font-sans text-small font-semibold tracking-normal">
            {t('connections.detail.previewTitle')}
          </h4>
          <Preview connectionId={connectionId} dataset={dataset} />
        </div>
      ) : null}
    </li>
  )
}

// ---------------------------------------------------------------------------------------------- versions
function VersionRow({
  version,
  timeZone,
  canManage,
  busy,
  onUse,
  onDiscard,
}: {
  version: FileVersion
  timeZone: string
  canManage: boolean
  busy: boolean
  onUse: () => void
  onDiscard: () => void
}) {
  const { t } = useTranslation()
  const when = formatRelativeTime(version.uploaded_at)
  const badge = version.in_use
    ? { text: t('connections.detail.inUse'), style: 'bg-gain-soft text-gain' }
    : version.status === 'failed'
      ? { text: t('connections.detail.failed'), style: 'bg-loss-soft text-loss' }
      : version.status === 'needs_confirmation'
        ? { text: t('connections.detail.needsReview'), style: 'bg-caution-soft text-caution' }
        : version.status === 'processing'
          ? { text: t('connections.detail.processing'), style: 'bg-neel-soft text-neel' }
          : null
  const usable =
    !version.in_use && (version.status === 'ready' || version.status === 'needs_confirmation')
  const discardable = !version.in_use && version.status !== 'processing'
  return (
    <li className="flex flex-col gap-2 border-b border-line py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="m-0 text-small font-medium text-ink">
            {t('connections.detail.version', { version: version.version })}
            <span className="ml-2 font-normal break-all text-ink-muted">
              {version.original_name}
            </span>
          </p>
          <p className="m-0 text-caption text-ink-muted tabular">
            {version.uploaded_by_name
              ? t('connections.detail.uploadedBy', { name: version.uploaded_by_name, when })
              : t('connections.detail.uploadedWhen', { when })}
            {' · '}
            {formatBytes(version.size_bytes)}
            <span className="sr-only"> ({formatDate(version.uploaded_at, timeZone)})</span>
          </p>
          {version.error ? (
            <p className="m-0 mt-1 text-caption text-loss">{version.error}</p>
          ) : null}
        </div>
        {badge ? (
          <span className={cn('rounded-pill px-3 py-1 text-caption font-semibold', badge.style)}>
            {badge.text}
          </span>
        ) : null}
      </div>
      {version.changes.length ? (
        <div className="text-caption text-ink">
          <p className="m-0">{t('connections.detail.changesIntro')}</p>
          <ul className="m-0 mt-1 list-disc pl-5">
            {version.changes.map((change, index) => (
              <li key={index}>{changeText(t, change)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {canManage && (usable || discardable) ? (
        <div className="flex flex-wrap gap-2">
          {usable ? (
            <Button
              size="sm"
              variant={version.status === 'needs_confirmation' ? 'primary' : 'secondary'}
              disabled={busy}
              aria-label={t('connections.detail.useVersionLabel', { version: version.version })}
              onClick={onUse}
            >
              {t('connections.detail.useVersion')}
            </Button>
          ) : null}
          {discardable ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              aria-label={t('connections.detail.discardLabel', { version: version.version })}
              onClick={onDiscard}
            >
              {t('connections.detail.discard')}
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  )
}

// -------------------------------------------------------------------------------------------------- page
function RenameDialog({
  connection,
  open,
  onOpenChange,
  onSaved,
}: {
  connection: ConnectionDetail
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (name: string) => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(connection.name)
  const [error, setError] = useState<string | undefined>()
  const save = useMutation({
    mutationFn: () => connectionsApi.update(connection.id, { name: name.trim() }),
    onSuccess: (updated) => onSaved(updated.name),
    onError: (caught) =>
      setError(
        caught instanceof ApiError
          ? (caught.fields.name ?? caught.message)
          : t('connections.loadFailed'),
      ),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim()) {
      setError(t('auth.required'))
      return
    }
    save.mutate()
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t('connections.detail.renameTitle', { name: connection.name })}>
        <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
          <Field
            label={t('connections.detail.nameLabel')}
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            error={error}
          />
          <div className="flex flex-wrap justify-end gap-2">
            <DialogClose asChild>
              <Button variant="secondary">{t('dialog.cancel')}</Button>
            </DialogClose>
            <Button type="submit" disabled={save.isPending}>
              {t('connections.detail.save')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function TestOutcome({ result }: { result: TestResult }) {
  const { t } = useTranslation()
  return (
    <FormAlert tone={result.ok ? 'success' : 'error'} className="mt-4">
      {result.message}
      {result.checks.length ? (
        <span className="mt-1 block text-caption">
          {result.checks
            .map(
              (check) =>
                `${check.ok ? '✓' : '✗'} ${t(`connections.detail.checks.${check.label}`, { defaultValue: check.label })}`,
            )
            .join(' · ')}
        </span>
      ) : null}
    </FormAlert>
  )
}

/** One connection (BUILD_SPEC 11 "Connections"): status, tables, columns, data notes, versions, actions. */
export function ConnectionDetailPage() {
  const { t } = useTranslation()
  const { connectionId = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const { permissions, organization } = useSession()
  const canManage = permissions.includes('connections.manage')
  const key = ['connections', connectionId] as const
  const detail = useQuery({
    queryKey: key,
    queryFn: () => connectionsApi.get(connectionId),
    refetchInterval: (query) => {
      const data = query.state.data
      if (data && isSyncing(data)) return POLL_MS
      // Tally: the PC pushes on its own; keep the page current while waiting for it.
      if (data?.tally)
        return data.tally.state === 'connected' && !data.tally.refresh_pending ? 60_000 : 5_000
      return false
    },
    retry: false,
  })
  const [notice, setNotice] = useState<Notice>(null)
  const [test, setTest] = useState<TestResult | null>(null)
  const [renaming, setRenaming] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [replacing, setReplacing] = useState(false)
  const [keyDialog, setKeyDialog] = useState(false)
  const [credentialsDialog, setCredentialsDialog] = useState(false)
  const [tallyCode, setTallyCode] = useState(false)
  const [discarding, setDiscarding] = useState<FileVersion | null>(null)
  const picker = useSpreadsheetPicker()
  // Shares the list's cache; only the upload limit is needed from it here.
  const sources = useQuery({ queryKey: CONNECTIONS, queryFn: connectionsApi.list })
  const maxMb = sources.data?.sources.upload_max_mb ?? 50
  const connection = detail.data

  useEffect(() => {
    document.title = `${connection?.name ?? t('pages.connections.title')} · ${t('app.name')}`
  }, [connection?.name, t])

  const refresh = () => client.invalidateQueries({ queryKey: CONNECTIONS })
  const fail = (error: unknown) =>
    setNotice({ tone: 'error', text: message(error, t('connections.loadFailed')) })
  const reread = useMutation({
    mutationFn: () => connectionsApi.refresh(connectionId),
    onSuccess: (updated) => {
      setNotice({
        tone: 'success',
        text: updated.tally
          ? t('connections.tally.refreshAsked')
          : t('connections.detail.refreshed'),
      })
      void refresh()
    },
    onError: fail,
  })
  const runTest = useMutation({
    mutationFn: () => connectionsApi.test(connectionId),
    onMutate: () => setTest(null),
    onSuccess: (result) => {
      setTest(result)
      void refresh()
    },
    onError: fail,
  })
  const pause = useMutation({
    mutationFn: (paused: boolean) => connectionsApi.update(connectionId, { paused }),
    onSuccess: (updated, paused) => {
      setNotice({
        tone: 'success',
        text: t(paused ? 'connections.detail.paused' : 'connections.detail.resumed', {
          name: updated.name,
        }),
      })
      void refresh()
    },
    onError: fail,
  })
  const remove = useMutation({
    mutationFn: () => connectionsApi.remove(connectionId),
    onSuccess: () => {
      void client.removeQueries({ queryKey: key })
      void refresh()
      void navigate('/connections', {
        state: { notice: t('connections.detail.removed', { name: connection?.name ?? '' }) },
      })
    },
    onError: (error) => {
      setRemoving(false)
      fail(error)
    },
  })
  const newVersion = useMutation({
    mutationFn: (file: File) => uploadFile(file, connectionId),
    onSuccess: (_, file) => {
      setNotice({ tone: 'success', text: t('connections.add.uploaded', { name: file.name }) })
      void refresh()
    },
    onError: fail,
  })
  const chooseVersion = useMutation({
    mutationFn: (version: FileVersion) => connectionsApi.chooseVersion(connectionId, version.id),
    onSuccess: (_, version) => {
      setNotice({
        tone: 'success',
        text: t('connections.detail.switching', { version: version.version }),
      })
      void refresh()
    },
    onError: fail,
  })
  const discardVersion = useMutation({
    mutationFn: (version: FileVersion) => connectionsApi.discardVersion(connectionId, version.id),
    onSuccess: (_, version) => {
      setDiscarding(null)
      setNotice({
        tone: 'success',
        text: t('connections.detail.discarded', { version: version.version }),
      })
      void refresh()
    },
    onError: (error) => {
      setDiscarding(null)
      fail(error)
    },
  })
  const reconnect = useMutation({
    mutationFn: () => connectionsApi.googleStart(connectionId),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    onError: fail,
  })
  const reconnectShopify = useMutation({
    mutationFn: () => connectionsApi.shopifyStart({ connection_id: connectionId }),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    onError: fail,
  })
  const reconnectZoho = useMutation({
    mutationFn: () => connectionsApi.zohoStart(connectionId),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    onError: fail,
  })
  const saveSheets = useMutation({
    mutationFn: (ids: string[]) => connectionsApi.setSpreadsheets(connectionId, ids),
    onSuccess: () => {
      setNotice({ tone: 'success', text: t('connections.detail.spreadsheetsSaved') })
      void refresh()
    },
    onError: fail,
  })
  const choose = useMutation({
    mutationFn: async () => {
      const config = await connectionsApi.pickerToken(connectionId)
      return picker.pick(config)
    },
    onSuccess: (picked) => {
      if (!picked.length || !connection?.google) return
      const ids = [...connection.google.spreadsheets.map((s) => s.id), ...picked.map((s) => s.id)]
      saveSheets.mutate([...new Set(ids)])
    },
    onError: fail,
  })

  if (detail.isPending) {
    return (
      <p role="status" className="m-0 text-small text-ink-muted">
        {t('connections.loading')}
      </p>
    )
  }
  if (detail.isError || !connection) {
    return (
      <>
        <PageHeader title={t('pages.connections.title')} />
        <FormAlert tone="error">{t('connections.detail.loadFailed')}</FormAlert>
        <Button asChild variant="link" className="mt-4 px-0">
          <Link to="/connections">{t('connections.back')}</Link>
        </Button>
      </>
    )
  }

  const syncing = isSyncing(connection)
  const paused = connection.status === 'disabled'
  const busy = syncing || reread.isPending || pause.isPending
  const added = connection.created_by_name
    ? t('connections.detail.addedBy', {
        name: connection.created_by_name,
        date: formatDate(connection.created_at, organization.timezone),
      })
    : t('connections.detail.added', {
        date: formatDate(connection.created_at, organization.timezone),
      })

  return (
    <>
      <Button asChild variant="link" size="sm" className="mb-2 -ml-1 px-1">
        <Link to="/connections">
          <ArrowLeft aria-hidden />
          {t('connections.back')}
        </Link>
      </Button>
      <PageHeader title={connection.name} intro={`${typeLabel(t, connection)} · ${added}`} />
      <div className="flex flex-col gap-6">
        <Section id="status-title" title={t('connections.detail.statusTitle')}>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <span
              className={cn(
                'rounded-pill px-3 py-1 text-caption font-semibold',
                statusStyle[connection.status],
              )}
            >
              {t(`connections.status.${connection.status}`)}
            </span>
            {syncing ? (
              <span role="status" className="flex items-center gap-2 text-small text-ink">
                <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden />
                {t('connections.reading')}
              </span>
            ) : (
              <span className="text-small text-ink-muted tabular">
                {connection.last_success_at
                  ? t('connections.updated', {
                      when: formatRelativeTime(connection.last_success_at),
                    })
                  : t('connections.notFetched')}
              </span>
            )}
          </div>
          {connection.google?.account_email ? (
            <p className="m-0 mt-2 text-small text-ink-muted">
              {t('connections.detail.account', { email: connection.google.account_email })}
            </p>
          ) : null}
          {connection.razorpay ? (
            <p className="m-0 mt-2 text-small text-ink-muted">
              {t('connections.razorpay.keyLine', { key: connection.razorpay.key_id_masked })}
              {' · '}
              {t(
                connection.razorpay.mode === 'test'
                  ? 'connections.razorpay.modeTest'
                  : 'connections.razorpay.modeLive',
              )}
              {'. '}
              {t('connections.razorpay.readOnlyNote')}
            </p>
          ) : null}
          {connection.shopify ? (
            <p className="m-0 mt-2 text-small text-ink-muted">
              {t('connections.shopify.storeLine', { shop: connection.shopify.shop })}
              {connection.shopify.currency
                ? ` · ${t('connections.shopify.currencyLine', { currency: connection.shopify.currency })}`
                : null}
              {'. '}
              {t(
                connection.shopify.all_orders
                  ? 'connections.shopify.allOrders'
                  : 'connections.shopify.recentOrders',
              )}{' '}
              {t('connections.shopify.readOnlyNote')}
            </p>
          ) : null}
          {connection.zoho ? <ZohoLines zoho={connection.zoho} /> : null}
          {connection.database ? <DatabaseLines database={connection.database} /> : null}
          {connection.tally ? <TallyLines tally={connection.tally} /> : null}
          {paused ? (
            <p className="m-0 mt-2 text-small text-ink-muted">
              {t('connections.detail.pausedNote')}
            </p>
          ) : null}
          {connection.last_error && connection.status !== 'active' ? (
            <FormAlert
              tone="error"
              className="mt-4"
              action={
                canManage &&
                connection.status === 'needs_reauth' &&
                connection.type === 'google_sheets' ? (
                  <Button
                    size="sm"
                    disabled={reconnect.isPending}
                    onClick={() => reconnect.mutate()}
                  >
                    {t('connections.detail.reconnectGoogle')}
                  </Button>
                ) : canManage &&
                  connection.status === 'needs_reauth' &&
                  connection.type === 'razorpay' ? (
                  <Button size="sm" onClick={() => setKeyDialog(true)}>
                    {t('connections.razorpay.reconnect')}
                  </Button>
                ) : canManage &&
                  connection.status === 'needs_reauth' &&
                  connection.type === 'shopify' ? (
                  <Button
                    size="sm"
                    disabled={reconnectShopify.isPending}
                    onClick={() => reconnectShopify.mutate()}
                  >
                    {t('connections.shopify.reconnect')}
                  </Button>
                ) : canManage &&
                  connection.status === 'needs_reauth' &&
                  connection.type === 'sql_database' ? (
                  <Button size="sm" onClick={() => setCredentialsDialog(true)}>
                    {t('connections.database.reconnect')}
                  </Button>
                ) : canManage &&
                  connection.status === 'needs_reauth' &&
                  connection.type === 'zoho_books' ? (
                  <Button
                    size="sm"
                    disabled={reconnectZoho.isPending}
                    onClick={() => reconnectZoho.mutate()}
                  >
                    {t('connections.zoho.reconnect')}
                  </Button>
                ) : null
              }
            >
              {connection.last_error}
            </FormAlert>
          ) : null}
          {connection.file?.latest_upload_needs_review ? (
            <QualityBanner className="mt-4">
              {t('connections.detail.reviewBanner', {
                version: connection.file.latest_upload_version ?? '',
              })}
            </QualityBanner>
          ) : null}
          {connection.file?.latest_upload_failed ? (
            <FormAlert tone="error" className="mt-4">
              {t('connections.detail.uploadFailed', {
                version: connection.file.version,
                reason: connection.file.latest_upload_error ?? '',
              })}
            </FormAlert>
          ) : null}
          {notice ? (
            <FormAlert tone={notice.tone} className="mt-4">
              {notice.text}
            </FormAlert>
          ) : null}
          {test ? <TestOutcome result={test} /> : null}
          {canManage && connection.zoho ? (
            <ZohoOrganizationPicker
              key={connection.zoho.organization_id}
              connectionId={connectionId}
              zoho={connection.zoho}
              disabled={busy}
              onDone={(text) => {
                setNotice({ tone: 'success', text })
                void refresh()
              }}
              onError={fail}
            />
          ) : null}
          {canManage ? (
            <div className="mt-4 flex flex-wrap gap-2">
              <Button variant="secondary" disabled={busy || paused} onClick={() => reread.mutate()}>
                <RefreshCw aria-hidden />
                {t('connections.detail.refresh')}
              </Button>
              <Button
                variant="secondary"
                disabled={runTest.isPending}
                onClick={() => runTest.mutate()}
              >
                {runTest.isPending ? t('connections.detail.testing') : t('connections.detail.test')}
              </Button>
              <Button variant="ghost" onClick={() => setRenaming(true)}>
                {t('connections.detail.rename')}
              </Button>
              <Button
                variant="ghost"
                disabled={pause.isPending}
                onClick={() => pause.mutate(!paused)}
              >
                {paused ? t('connections.detail.resume') : t('connections.detail.pause')}
              </Button>
              {connection.type === 'razorpay' ? (
                <Button variant="ghost" onClick={() => setKeyDialog(true)}>
                  {t('connections.razorpay.replaceButton')}
                </Button>
              ) : null}
              {connection.type === 'shopify' ? (
                <Button
                  variant="ghost"
                  disabled={reconnectShopify.isPending}
                  onClick={() => reconnectShopify.mutate()}
                >
                  {t('connections.shopify.reconnect')}
                </Button>
              ) : null}
              {connection.type === 'sql_database' ? (
                <Button variant="ghost" onClick={() => setCredentialsDialog(true)}>
                  {t('connections.database.replaceButton')}
                </Button>
              ) : null}
              {connection.type === 'zoho_books' ? (
                <Button
                  variant="ghost"
                  disabled={reconnectZoho.isPending}
                  onClick={() => reconnectZoho.mutate()}
                >
                  {t('connections.zoho.reconnect')}
                </Button>
              ) : null}
              {connection.type === 'tally' ? (
                <Button variant="ghost" onClick={() => setTallyCode(true)}>
                  {t('connections.tally.newCode')}
                </Button>
              ) : null}
              {connection.tally?.device_name && connection.tally.state !== 'not_paired' ? (
                <UnpairTally
                  connectionId={connectionId}
                  pcName={connection.tally.device_name}
                  onDone={(text) => setNotice({ tone: 'success', text })}
                  onError={fail}
                />
              ) : null}
              <Button variant="ghost" onClick={() => setReplacing(true)}>
                {t('dictionary.replace.button')}
              </Button>
              <Button variant="ghost" onClick={() => setRemoving(true)}>
                {t('connections.detail.remove')}
              </Button>
            </div>
          ) : null}
        </Section>

        {connection.type === 'sql_database' && canManage ? (
          <Section
            id="database-tables-title"
            title={t('connections.database.tablesTitle')}
            intro={t('connections.database.tablesIntro', { max: 100 })}
          >
            <DatabaseTables
              connectionId={connectionId}
              chosen={connection.database?.tables ?? []}
              disabled={busy}
              onSaved={() => void refresh()}
            />
          </Section>
        ) : null}

        {connection.type === 'google_sheets' && canManage ? (
          <Section id="sheets-title" title={t('connections.detail.spreadsheetsTitle')}>
            {connection.google?.spreadsheets.length ? (
              <ul className="m-0 mt-2 list-none p-0">
                {connection.google.spreadsheets.map((sheet) => (
                  <li
                    key={sheet.id}
                    className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-line py-2 last:border-b-0"
                  >
                    <span className="min-w-0 text-small break-words text-ink">{sheet.name}</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={saveSheets.isPending}
                      aria-label={t('connections.detail.removeSpreadsheet', { name: sheet.name })}
                      onClick={() =>
                        saveSheets.mutate(
                          (connection.google?.spreadsheets ?? [])
                            .filter((s) => s.id !== sheet.id)
                            .map((s) => s.id),
                        )
                      }
                    >
                      {t('connections.detail.remove')}
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="m-0 mt-2 text-small text-ink-muted">
                {t('connections.detail.spreadsheetsEmpty')}
              </p>
            )}
            <Button
              className="mt-4"
              disabled={
                choose.isPending || saveSheets.isPending || connection.status === 'needs_reauth'
              }
              onClick={() => choose.mutate()}
            >
              {choose.isPending
                ? t('connections.detail.choosing')
                : t('connections.detail.chooseSpreadsheets')}
            </Button>
          </Section>
        ) : null}

        <Section id="tables-title" title={t('connections.detail.tablesTitle')}>
          {connection.datasets.length ? (
            <ul className="m-0 mt-2 list-none p-0" aria-label={t('connections.detail.tablesTitle')}>
              {connection.datasets.map((dataset) => (
                <DatasetRow key={dataset.id} connectionId={connection.id} dataset={dataset} />
              ))}
            </ul>
          ) : (
            <p className="m-0 mt-2 text-small text-ink-muted">
              {t('connections.detail.tablesEmpty')}
            </p>
          )}
        </Section>

        {connection.type === 'file' ? (
          <Section
            id="versions-title"
            title={t('connections.detail.versionsTitle')}
            intro={canManage ? t('connections.detail.newVersionHint') : undefined}
          >
            {canManage ? (
              <div className="mt-4">
                <FileDrop
                  busy={newVersion.isPending || syncing}
                  label={t('connections.detail.newVersion')}
                  onFile={(file) => {
                    const problem = fileProblem(file, maxMb, t)
                    if (problem) setNotice({ tone: 'error', text: problem })
                    else newVersion.mutate(file)
                  }}
                />
              </div>
            ) : null}
            <ul
              className="m-0 mt-2 list-none p-0"
              aria-label={t('connections.detail.versionsTitle')}
            >
              {connection.versions.map((version) => (
                <VersionRow
                  key={version.id}
                  version={version}
                  timeZone={organization.timezone}
                  canManage={canManage}
                  busy={syncing || chooseVersion.isPending || discardVersion.isPending}
                  onUse={() => chooseVersion.mutate(version)}
                  onDiscard={() => setDiscarding(version)}
                />
              ))}
            </ul>
          </Section>
        ) : null}
      </div>
      {renaming ? (
        <RenameDialog
          connection={connection}
          open
          onOpenChange={setRenaming}
          onSaved={(name) => {
            setRenaming(false)
            setNotice({ tone: 'success', text: t('connections.detail.renamed', { name }) })
            void refresh()
          }}
        />
      ) : null}
      {tallyCode ? (
        <Suspense fallback={null}>
          <TallyDialog
            open
            onOpenChange={(next) => {
              setTallyCode(next)
              if (!next) void refresh()
            }}
            connectionId={connectionId}
          />
        </Suspense>
      ) : null}
      {keyDialog ? (
        <Suspense fallback={null}>
          <RazorpayDialog
            open
            onOpenChange={setKeyDialog}
            connection={connection}
            onDone={(text) => setNotice({ tone: 'success', text })}
          />
        </Suspense>
      ) : null}
      {credentialsDialog ? (
        <Suspense fallback={null}>
          <DatabaseDialog
            open
            onOpenChange={setCredentialsDialog}
            connection={connection}
            onDone={(text) => {
              setNotice({ tone: 'success', text })
              void refresh()
            }}
          />
        </Suspense>
      ) : null}
      {replacing ? (
        <ReplaceDialog connection={connection} open onOpenChange={setReplacing} />
      ) : null}
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={t('connections.detail.removeTitle', { name: connection.name })}
        description={t('connections.detail.removeBody')}
        confirmLabel={t('connections.detail.removeConfirm', { name: connection.name })}
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      >
        <RemoveImpact impact={connection.dictionary_impact} />
      </ConfirmDialog>
      <ConfirmDialog
        open={discarding !== null}
        onOpenChange={(open) => !open && setDiscarding(null)}
        title={t('connections.detail.discardTitle', { version: discarding?.version ?? '' })}
        description={t('connections.detail.discardBody')}
        confirmLabel={t('connections.detail.discardConfirm', {
          version: discarding?.version ?? '',
        })}
        busy={discardVersion.isPending}
        onConfirm={() => discarding && discardVersion.mutate(discarding)}
      />
      {picker.element}
    </>
  )
}

/** The warning before removal: what happens to the business dictionary's metrics and joins (D64). */
function RemoveImpact({ impact }: { impact: DictionaryImpact | null | undefined }) {
  const { t } = useTranslation()
  if (!impact) return null
  const orphaned = impact.orphaned_metrics
  return (
    <div className="mt-3 border-t border-line pt-3 text-small">
      <h3 className="m-0 text-small font-medium">{t('connections.detail.removeDictionary')}</h3>
      <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0 text-ink-muted">
        {impact.moves.map((move) => (
          <li key={`${move.table}-${move.to}`}>
            {t('connections.detail.removeMoves', {
              table: move.table,
              to: move.to,
              count: move.metrics,
            })}
          </li>
        ))}
        {orphaned.length > 0 ? (
          <li className="text-caution">
            {t('connections.detail.removeOrphaned', {
              count: orphaned.length,
              names: orphaned.join(', '),
            })}
          </li>
        ) : null}
        <li>{t('connections.detail.removeEdits')}</li>
        {impact.joins > 0 ? (
          <li>{t('connections.detail.removeJoins', { count: impact.joins })}</li>
        ) : null}
      </ul>
    </div>
  )
}
