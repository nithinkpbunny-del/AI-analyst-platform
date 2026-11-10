import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { Field } from '../../components/ui/input'
import { SelectField } from '../../components/ui/select'
import { useSession } from '../../lib/authContext'
import { formatDateTime } from '../../lib/format'
import { useCan } from '../../lib/permissions'
import { downloadAuditCsv, teamApi, type AuditEntry, type AuditFilters } from '../../lib/teamApi'
import { asKey, describeEntry } from './describe'

const GROUPS = [
  'auth.login',
  'auth.2fa',
  'auth.session',
  'team',
  'org',
  'llm',
  'connection',
  'audit',
] as const

function Filters({
  filters,
  onChange,
  people,
}: {
  filters: AuditFilters
  onChange: (filters: AuditFilters) => void
  people: { id: string; name: string }[] | null
}) {
  const { t } = useTranslation()
  const set = (key: keyof AuditFilters) => (value: string) =>
    onChange({ ...filters, [key]: value || undefined })
  const any = Object.values(filters).some(Boolean)
  return (
    <fieldset className="m-0 mb-6 grid gap-4 rounded-panel border border-line bg-surface p-4 tablet:grid-cols-2 desktop:grid-cols-[repeat(4,minmax(0,1fr))_auto] desktop:items-end">
      <legend className="sr-only">{t('activity.filters')}</legend>
      {people ? (
        <SelectField
          label={t('activity.person')}
          value={filters.user_id ?? ''}
          onChange={(event) => set('user_id')(event.target.value)}
        >
          <option value="">{t('activity.everyone')}</option>
          {people.map((person) => (
            <option key={person.id} value={person.id}>
              {person.name}
            </option>
          ))}
        </SelectField>
      ) : null}
      <SelectField
        label={t('activity.what')}
        value={filters.action ?? ''}
        onChange={(event) => set('action')(event.target.value)}
      >
        <option value="">{t('activity.everything')}</option>
        {GROUPS.map((group) => (
          <option key={group} value={group}>
            {t(`activity.groups.${asKey(group)}`)}
          </option>
        ))}
      </SelectField>
      <Field
        label={t('activity.from')}
        type="date"
        value={filters.from ?? ''}
        max={filters.to}
        onChange={(event) => set('from')(event.target.value)}
      />
      <Field
        label={t('activity.to')}
        type="date"
        value={filters.to ?? ''}
        min={filters.from}
        onChange={(event) => set('to')(event.target.value)}
      />
      <Button variant="ghost" disabled={!any} onClick={() => onChange({})} className="w-fit">
        {t('activity.clear')}
      </Button>
    </fieldset>
  )
}

function EntryRow({ entry, timeZone }: { entry: AuditEntry; timeZone: string }) {
  const { t } = useTranslation()
  const where = [entry.ip, entry.browser].filter(Boolean).join(' · ')
  return (
    <li className="grid gap-x-6 gap-y-0.5 border-b border-line py-3 last:border-b-0 tablet:min-h-11 tablet:grid-cols-[176px_minmax(0,200px)_minmax(0,1fr)] tablet:items-baseline">
      <p className="m-0 text-caption text-ink-muted tabular-nums tablet:text-small">
        <time dateTime={entry.at}>{formatDateTime(entry.at, timeZone)}</time>
      </p>
      <p className="m-0 truncate text-small font-medium text-ink">
        {entry.user_name ?? t('activity.nobody')}
      </p>
      <div className="min-w-0">
        <p className="m-0 text-small text-ink">{describeEntry(t, entry)}</p>
        {where ? (
          <p className="m-0 truncate text-caption text-ink-muted tabular-nums">{where}</p>
        ) : null}
      </div>
    </li>
  )
}

/** Activity log (BUILD_SPEC 6.7, 11): filterable ledger and CSV export. Analysts see their own entries. */
export function ActivityPage() {
  const { t } = useTranslation()
  const { organization } = useSession()
  const canSeeEveryone = useCan('audit.view_all')
  const canListPeople = useCan('members.manage')
  const [filters, setFilters] = useState<AuditFilters>({})
  const [exporting, setExporting] = useState(false)
  const [exportProblem, setExportProblem] = useState<string | null>(null)
  useEffect(() => {
    document.title = `${t('pages.activity.title')} · ${t('app.name')}`
  }, [t])

  const people = useQuery({
    queryKey: ['team', 'members'],
    queryFn: teamApi.members,
    enabled: canSeeEveryone && canListPeople,
  })
  const log = useInfiniteQuery({
    queryKey: ['audit', filters],
    queryFn: ({ pageParam }) => teamApi.audit(filters, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const entries = log.data?.pages.flatMap((page) => page.entries) ?? []
  const scope = log.data?.pages[0]?.scope

  const exportCsv = async () => {
    setExporting(true)
    setExportProblem(null)
    try {
      await downloadAuditCsv(filters)
    } catch {
      setExportProblem(t('activity.exportFailed'))
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <PageHeader title={t('pages.activity.title')} intro={t('pages.activity.intro')}>
        <Button variant="secondary" disabled={exporting} onClick={() => void exportCsv()}>
          <Download aria-hidden />
          {exporting ? t('activity.exporting') : t('activity.exportCsv')}
        </Button>
      </PageHeader>
      {exportProblem ? (
        <FormAlert tone="error" className="mb-4">
          {exportProblem}
        </FormAlert>
      ) : null}
      {scope === 'own' ? (
        <FormAlert tone="info" className="mb-4">
          {t('activity.ownOnly')}
        </FormAlert>
      ) : null}
      <Filters
        filters={filters}
        onChange={setFilters}
        people={
          people.data ? people.data.members.map((m) => ({ id: m.user_id, name: m.name })) : null
        }
      />
      <section
        aria-label={t('pages.activity.title')}
        className="rounded-panel border border-line bg-surface px-4 py-2 tablet:px-6"
      >
        <div
          aria-hidden
          className="hidden grid-cols-[176px_minmax(0,200px)_minmax(0,1fr)] gap-x-6 border-b border-line py-2 text-caption font-medium text-ink-muted tablet:grid"
        >
          <span>{t('activity.columns.when')}</span>
          <span>{t('activity.columns.who')}</span>
          <span>{t('activity.columns.what')}</span>
        </div>
        {log.isPending ? (
          <p role="status" className="m-0 py-3 text-small text-ink-muted">
            {t('activity.loading')}
          </p>
        ) : null}
        {log.isError ? (
          <FormAlert tone="error" className="my-3">
            {t('activity.loadFailed')}
          </FormAlert>
        ) : null}
        {log.isSuccess && !entries.length ? (
          <p className="m-0 py-3 text-small text-ink-muted">{t('activity.empty')}</p>
        ) : null}
        {entries.length ? (
          <ul className="m-0 list-none p-0" aria-label={t('pages.activity.title')}>
            {entries.map((entry) => (
              <EntryRow key={entry.id} entry={entry} timeZone={organization.timezone} />
            ))}
          </ul>
        ) : null}
      </section>
      {log.hasNextPage ? (
        <Button
          variant="secondary"
          className="mt-4"
          disabled={log.isFetchingNextPage}
          onClick={() => void log.fetchNextPage()}
        >
          {t('activity.loadMore')}
        </Button>
      ) : null}
    </>
  )
}
