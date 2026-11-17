import './strings'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { QualityBanner } from '../../components/QualityBanner'
import { Button } from '../../components/ui/button'
import { CheckboxField } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { connectionsApi, type DatabaseInfo, type DatabaseTable } from '../../lib/connectionsApi'
import { formatNumber } from '../../lib/format'

/** Where the database is, how the connection is encrypted, and the write warning (M14). */
export function DatabaseLines({ database }: { database: DatabaseInfo }) {
  const { t } = useTranslation()
  return (
    <>
      <p className="m-0 mt-2 text-small break-words text-ink-muted">
        {t('connections.database.line', {
          engine: t(`connections.database.engines.${database.engine}`),
          host: database.host,
          port: database.port,
          database: database.database,
          user: database.username_masked,
        })}
        {'. '}
        {t(`connections.database.sslLine.${database.ssl_mode}`)}{' '}
        {t('connections.database.readOnlyNote')}
      </p>
      {database.can_write ? (
        <QualityBanner className="mt-4">{t('connections.database.canWrite')}</QualityBanner>
      ) : null}
    </>
  )
}

/** Owners choose which tables and views questions may use; only those are read and queried. */
export function DatabaseTables({
  connectionId,
  chosen,
  disabled,
  onSaved,
}: {
  connectionId: string
  chosen: string[]
  disabled: boolean
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const listed = useQuery({
    queryKey: ['connections', connectionId, 'database-tables'],
    queryFn: () => connectionsApi.databaseTables(connectionId),
  })
  const [picked, setPicked] = useState<Set<string> | null>(null)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const selection = picked ?? new Set(chosen)
  const max = listed.data?.max_tables ?? 100
  const save = useMutation({
    mutationFn: () => connectionsApi.chooseDatabaseTables(connectionId, [...selection]),
    onSuccess: () => {
      setPicked(null)
      setNotice({ tone: 'success', text: t('connections.database.tablesSaved') })
      onSaved()
    },
    onError: (error) =>
      setNotice({
        tone: 'error',
        text: error instanceof ApiError ? error.message : t('connections.database.tablesFailed'),
      }),
  })
  const toggle = (table: DatabaseTable, on: boolean) => {
    const next = new Set(selection)
    if (on) next.add(table.ref)
    else next.delete(table.ref)
    setPicked(next)
    setNotice(null)
  }
  const tooMany = selection.size > max

  return (
    <div className="mt-2">
      {chosen.length === 0 ? (
        <p className="m-0 text-small text-ink">{t('connections.database.tablesNone')}</p>
      ) : null}
      {notice ? (
        <FormAlert tone={notice.tone} className="mt-4">
          {notice.text}
        </FormAlert>
      ) : null}
      {listed.isPending ? (
        <p role="status" className="m-0 mt-4 text-small text-ink-muted">
          {t('connections.database.tablesLoading')}
        </p>
      ) : listed.isError ? (
        <FormAlert tone="error" className="mt-4">
          {listed.error instanceof ApiError
            ? listed.error.message
            : t('connections.database.tablesFailed')}
        </FormAlert>
      ) : listed.data.tables.length === 0 ? (
        <p className="m-0 mt-4 text-small text-ink-muted">
          {t('connections.database.tablesEmpty')}
        </p>
      ) : (
        <>
          <ul className="m-0 mt-4 list-none p-0" aria-label={t('connections.database.tablesTitle')}>
            {listed.data.tables.map((table) => (
              <li
                key={table.ref}
                className="flex min-h-11 flex-wrap items-center justify-between gap-x-4 border-b border-line py-1 last:border-b-0"
              >
                <CheckboxField
                  name={`table-${table.ref}`}
                  label={table.name}
                  checked={selection.has(table.ref)}
                  onChange={(on) => toggle(table, on)}
                />
                <span className="text-caption text-ink-muted tabular">
                  {t(`connections.database.kind.${table.kind}`)}
                  {table.rows_estimate !== null
                    ? ` · ${t('connections.database.rows', { rows: formatNumber(table.rows_estimate) })}`
                    : null}
                </span>
              </li>
            ))}
          </ul>
          {tooMany ? (
            <FormAlert tone="error" className="mt-4">
              {t('connections.database.tooMany', { max })}
            </FormAlert>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              disabled={disabled || save.isPending || picked === null || tooMany}
              onClick={() => save.mutate()}
            >
              {save.isPending
                ? t('connections.database.savingTables')
                : t('connections.database.saveTables')}
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
