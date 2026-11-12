import { DraftNotice } from '../dictionary/DraftNotice'
import { useQuery } from '@tanstack/react-query'
import { Plug } from 'lucide-react'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router'
import { ConnectionCard } from '../../components/ConnectionCard'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { useSession } from '../../lib/authContext'
import { connectionsApi, isSyncing } from '../../lib/connectionsApi'
import { AccessSection } from './AccessSection'
import { AddData } from './AddData'
import { CONNECTIONS } from './keys'
import { summary, typeLabel } from './text'

const POLL_MS = 2000

/** Connections (BUILD_SPEC 11): every data source with status and freshness; Owners add and manage them. */
export function ConnectionsPage() {
  const { t } = useTranslation()
  const { permissions, organization } = useSession()
  const canManage = permissions.includes('connections.manage')
  const list = useQuery({
    queryKey: CONNECTIONS,
    queryFn: connectionsApi.list,
    // While a source is being read in the background, keep the list fresh.
    refetchInterval: (query) => (query.state.data?.connections.some(isSyncing) ? POLL_MS : false),
  })
  useEffect(() => {
    document.title = `${t('pages.connections.title')} · ${t('app.name')}`
  }, [t])
  const connections = list.data?.connections ?? []
  const removedNotice = (useLocation().state as { notice?: string } | null)?.notice

  return (
    <>
      <PageHeader
        title={t('pages.connections.title')}
        intro={canManage ? t('pages.connections.intro') : t('connections.analystIntro')}
      />
      <div className="flex flex-col gap-6">
        {removedNotice ? <FormAlert tone="success">{removedNotice}</FormAlert> : null}
        <DraftNotice />
        {canManage && list.data ? <AddData sources={list.data.sources} /> : null}
        {list.isPending ? (
          <p role="status" className="m-0 text-small text-ink-muted">
            {t('connections.loading')}
          </p>
        ) : null}
        {list.isError ? <FormAlert tone="error">{t('connections.loadFailed')}</FormAlert> : null}
        {list.isSuccess && !connections.length ? (
          <EmptyState
            className="max-w-2xl"
            icon={Plug}
            message={
              canManage
                ? t('connections.empty')
                : t('connections.emptyAnalyst', { org: organization.name })
            }
          />
        ) : null}
        {connections.length ? (
          <section aria-labelledby="sources-title">
            <h2 id="sources-title" className="mb-3 text-h4">
              {t('connections.yours')}
            </h2>
            <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 tablet:grid-cols-2 desktop:grid-cols-3">
              {connections.map((connection) => (
                <li key={connection.id} className="min-w-0">
                  <ConnectionCard
                    className="h-full"
                    name={connection.name}
                    title={
                      <Link
                        to={`/connections/${connection.id}`}
                        className="text-ink underline-offset-4 hover:underline"
                        aria-label={t('connections.open', { name: connection.name })}
                      >
                        {connection.name}
                      </Link>
                    }
                    sourceType={typeLabel(t, connection)}
                    status={connection.status}
                    lastSuccessAt={connection.last_success_at}
                    reading={isSyncing(connection)}
                    summary={summary(t, connection)}
                    warnings={connection.warnings_count}
                    message={connection.status === 'active' ? null : connection.last_error}
                  />
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {canManage && list.isSuccess ? <AccessSection connections={connections} /> : null}
        <p className="m-0 max-w-prose text-caption text-ink-muted">
          {t('connections.readOnlyNote')}
        </p>
      </div>
    </>
  )
}
