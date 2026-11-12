import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Download, LoaderCircle } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { useSession } from '../../lib/authContext'
import {
  DICTIONARY,
  dictionaryApi,
  downloadYaml,
  type DictionaryContent,
  type DictionaryVersion,
} from '../../lib/dictionaryApi'
import { formatDate } from '../../lib/format'
import { DraftWorkspace } from './DraftWorkspace'
import { JoinsSection, MetricsSection } from './MetricsAndJoins'
import { TableCard } from './TableCard'
import { errorText, versionLine, type Notice } from './text'

const POLL_MS = 2500
function Banner({
  tone,
  title,
  children,
}: {
  tone: 'caution' | 'neutral'
  title?: string
  children: ReactNode
}) {
  return (
    <div
      role="status"
      className={
        tone === 'caution'
          ? 'rounded-panel border border-caution bg-caution-soft p-4 text-small'
          : 'rounded-panel border border-line bg-surface p-4 text-small'
      }
    >
      {title ? <p className="m-0 font-medium">{title}</p> : null}
      <div className={title ? 'mt-1' : undefined}>{children}</div>
    </div>
  )
}

function ReadOnly({ content }: { content: DictionaryContent }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-h4">{t('dictionary.tables.title')}</h2>
      {Object.entries(content.datasets).map(([key, table]) => (
        <TableCard key={key} tableKey={key} table={table} />
      ))}
      <MetricsSection content={content} />
      <JoinsSection content={content} />
    </div>
  )
}

function History({
  versions,
  onExport,
}: {
  versions: DictionaryVersion[]
  onExport: (id: string) => void
}) {
  const { t } = useTranslation()
  if (versions.length === 0) return null
  return (
    <section
      aria-labelledby="dictionary-history"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="dictionary-history" className="text-h4">
        {t('dictionary.history.title')}
      </h2>
      <ul className="m-0 mt-4 list-none p-0">
        {versions.map((version) => (
          <li
            key={version.id}
            className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-3 last:border-b-0"
          >
            <div>
              <p className="m-0 text-small font-medium">
                {t('dictionary.history.version', { version: version.version })}{' '}
                <span className="font-normal text-ink-muted">
                  ·{' '}
                  {version.status === 'confirmed'
                    ? t('dictionary.history.current')
                    : t('dictionary.history.superseded')}
                </span>
              </p>
              <p className="m-0 text-caption text-ink-muted">
                {version.confirmed_at
                  ? version.confirmed_by_name
                    ? t('dictionary.history.confirmed', {
                        date: formatDate(version.confirmed_at),
                        name: version.confirmed_by_name,
                      })
                    : t('dictionary.history.confirmedNoName', {
                        date: formatDate(version.confirmed_at),
                      })
                  : ''}
                {' · '}
                {t(`dictionary.history.origins.${version.origin}`)}
              </p>
            </div>
            <Button variant="ghost" size="sm" onClick={() => onExport(version.id)}>
              <Download aria-hidden />
              {t('dictionary.actions.export')}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Business dictionary (BUILD_SPEC 9, 11): review the draft, edit, confirm; read-only for others. */
export function DictionaryPage() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const canEdit = permissions.includes('dictionary.edit')
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<Notice>(null)

  const query = useQuery({
    queryKey: DICTIONARY,
    queryFn: dictionaryApi.get,
    refetchInterval: (q) => (q.state.data?.drafting ? POLL_MS : false),
  })
  const state = query.data
  const draft = canEdit ? (state?.draft ?? null) : null
  const refresh = () => queryClient.invalidateQueries({ queryKey: DICTIONARY })

  const redraft = useMutation({
    mutationFn: (freshAi: boolean) => dictionaryApi.requestDraft(freshAi),
    onSuccess: async () => {
      setNotice(null)
      await refresh()
    },
    onError: (error) => setNotice({ tone: 'error', text: errorText(t, error) }),
  })

  const exportYaml = async (versionId?: string) => {
    try {
      await downloadYaml(versionId)
    } catch {
      setNotice({ tone: 'error', text: t('dictionary.actions.exportFailed') })
    }
  }

  if (query.isPending) {
    return <p className="text-ink-muted">{t('dictionary.loading')}</p>
  }
  if (query.isError || !state) {
    return <FormAlert tone="error">{t('dictionary.loadFailed')}</FormAlert>
  }

  const active = state.active
  const header = (
    <PageHeader
      title={t('dictionary.title')}
      intro={canEdit ? t('dictionary.intro') : t('dictionary.introReader')}
    >
      {active || draft ? (
        <Button variant="secondary" onClick={() => void exportYaml()}>
          <Download aria-hidden />
          {t('dictionary.actions.export')}
        </Button>
      ) : null}
    </PageHeader>
  )

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      {header}
      {notice ? (
        <FormAlert tone={notice.tone === 'error' ? 'error' : 'success'}>{notice.text}</FormAlert>
      ) : null}
      {state.drafting ? (
        <Banner tone="neutral">
          <span className="inline-flex items-center gap-2">
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
            {t('dictionary.drafting')}
          </span>
        </Banner>
      ) : null}
      {!state.confirmed && (active || draft) ? (
        <Banner tone="caution" title={t('dictionary.notConfirmed.title')}>
          {t('dictionary.notConfirmed.body')}
        </Banner>
      ) : null}
      {state.confirmed && active ? (
        <p className="m-0 text-small text-ink-muted">
          {versionLine(t, active, formatDate(active.confirmed_at ?? active.created_at))}
        </p>
      ) : null}
      {draft && state.confirmed ? (
        <Banner tone="caution" title={t('dictionary.draftNotice.title')}>
          {t('dictionary.draftNotice.body', { version: active?.version })}
        </Banner>
      ) : null}

      {!active && !draft ? (
        state.drafting ? null : (
          <EmptyState
            icon={BookOpen}
            message={canEdit ? t('dictionary.empty') : t('dictionary.emptyReader')}
            action={
              canEdit ? (
                <Button asChild>
                  <Link to="/connections">{t('dictionary.goConnections')}</Link>
                </Button>
              ) : undefined
            }
          />
        )
      ) : null}

      {draft ? (
        <DraftWorkspace
          key={`${draft.id}:${JSON.stringify(draft.content)}`}
          draft={draft}
          busy={redraft.isPending || state.drafting}
          onNotice={setNotice}
          onRedraft={() => redraft.mutate(true)}
        />
      ) : null}

      {!draft && active ? (
        <>
          <ReadOnly content={active.content} />
          {canEdit ? (
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                disabled={redraft.isPending || state.drafting}
                onClick={() => redraft.mutate(false)}
              >
                {redraft.isPending
                  ? t('dictionary.actions.redrafting')
                  : t('dictionary.actions.redraft')}
              </Button>
              <Button
                variant="secondary"
                disabled={redraft.isPending || state.drafting}
                onClick={() => redraft.mutate(true)}
              >
                {t('dictionary.actions.redraftAi')}
              </Button>
            </div>
          ) : null}
        </>
      ) : null}

      {canEdit ? (
        <History versions={state.versions} onExport={(id) => void exportYaml(id)} />
      ) : null}
    </div>
  )
}
