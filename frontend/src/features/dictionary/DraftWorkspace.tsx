import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { Input } from '../../components/ui/input'
import { Select } from '../../components/ui/select'
import { ApiError } from '../../lib/api'
import {
  DICTIONARY,
  INDUSTRIES,
  dictionaryApi,
  type DictionaryContent,
  type DictionaryVersionContent,
} from '../../lib/dictionaryApi'
import { JoinsSection, MetricsSection } from './MetricsAndJoins'
import { TableCard } from './TableCard'
import { aiNote, changeText, columnCount, errorText, pendingItems, type Notice } from './text'

function Summary({
  draft,
  content,
}: {
  draft: DictionaryVersionContent
  content: DictionaryContent
}) {
  const { t } = useTranslation()
  const pending = pendingItems(content)
  const dropped = draft.notes.dropped_metrics?.length ?? 0
  const joins = draft.notes.dropped_joins ?? 0
  const note = aiNote(t, draft.notes)
  const unread = draft.notes.unreadable_tables ?? []
  return (
    <section
      aria-labelledby="dictionary-summary"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="dictionary-summary" className="text-h4">
        {t('dictionary.summary', {
          tables: Object.keys(content.datasets).length,
          columns: columnCount(content),
        })}
      </h2>
      <p className="m-0 mt-2 text-body">
        {pending > 0
          ? t('dictionary.summaryReview', { count: pending })
          : t('dictionary.summaryClear')}
      </p>
      {note ? <p className="m-0 mt-2 max-w-prose text-small text-ink-muted">{note}</p> : null}
      {dropped > 0 ? (
        <p className="m-0 mt-2 text-small text-ink-muted">
          {t('dictionary.dropped.metrics', { count: dropped })}
        </p>
      ) : null}
      {joins > 0 ? (
        <p className="m-0 mt-2 text-small text-ink-muted">
          {t('dictionary.dropped.joins', { count: joins })}
        </p>
      ) : null}
      {unread.length > 0 ? (
        <p className="m-0 mt-2 text-small text-caution">
          {t('dictionary.dropped.unreadable', { names: unread.join(', ') })}
        </p>
      ) : null}
      {draft.diff.length > 0 ? (
        <div className="mt-4 border-t border-line pt-4">
          <h3 className="m-0 text-small font-medium">{t('dictionary.changes.title')}</h3>
          <ul className="m-0 mt-2 list-disc pl-5 text-small">
            {draft.diff.map((item, index) => (
              <li key={`${item.kind}-${item.dataset}-${item.column}-${index}`}>
                {changeText(t, item)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  )
}

function CompanySection({
  content,
  onChange,
}: {
  content: DictionaryContent
  onChange: (next: DictionaryContent) => void
}) {
  const { t } = useTranslation()
  return (
    <section
      aria-labelledby="dictionary-company"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="dictionary-company" className="text-h4">
        {t('dictionary.company.title')}
      </h2>
      <div className="mt-4 grid gap-3 tablet:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-small font-medium">
          {t('dictionary.company.name')}
          <Input
            value={content.company.name}
            maxLength={120}
            onChange={(event) =>
              onChange({ ...content, company: { ...content.company, name: event.target.value } })
            }
          />
        </label>
        <label className="flex flex-col gap-1.5 text-small font-medium">
          {t('dictionary.company.industry')}
          <Select
            value={content.company.industry}
            onChange={(event) =>
              onChange({
                ...content,
                company: {
                  ...content.company,
                  industry: event.target.value as DictionaryContent['company']['industry'],
                },
              })
            }
          >
            {INDUSTRIES.map((industry) => (
              <option key={industry} value={industry}>
                {t(`dictionary.company.industries.${industry}`)}
              </option>
            ))}
          </Select>
        </label>
      </div>
    </section>
  )
}

/**
 * The Owner's working copy of the draft. It is created from the server's draft (the page gives it a `key`, so a
 * new draft or a save starts it afresh) and edited locally until saved or confirmed.
 */
export function DraftWorkspace({
  draft,
  busy,
  onNotice,
  onRedraft,
}: {
  draft: DictionaryVersionContent
  /** A new draft is being made or asked for. */
  busy: boolean
  onNotice: (notice: Notice) => void
  onRedraft: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [content, setContent] = useState<DictionaryContent>(draft.content)
  const [discarding, setDiscarding] = useState(false)
  const dirty = JSON.stringify(content) !== JSON.stringify(draft.content)
  const pending = pendingItems(content)
  const refresh = () => queryClient.invalidateQueries({ queryKey: DICTIONARY })

  const save = useMutation({
    mutationFn: () => dictionaryApi.save(content),
    onSuccess: async () => {
      onNotice({ tone: 'success', text: t('dictionary.actions.saved') })
      await refresh()
    },
    onError: (error) => onNotice({ tone: 'error', text: errorText(t, error) }),
  })
  const confirm = useMutation({
    mutationFn: async () => {
      if (dirty) {
        // Saving also tests the metrics again; they may still not work.
        const saved = await dictionaryApi.save(content)
        if (saved.review_items > 0) {
          await refresh()
          throw new ApiError(409, 'review_pending', '')
        }
      }
      return dictionaryApi.confirm()
    },
    onSuccess: async (confirmed) => {
      onNotice({
        tone: 'success',
        text: t('dictionary.actions.confirmed', { version: confirmed.version }),
      })
      await refresh()
    },
    onError: (error) => onNotice({ tone: 'error', text: errorText(t, error) }),
  })
  const discard = useMutation({
    mutationFn: () => dictionaryApi.discard(),
    onSuccess: async () => {
      setDiscarding(false)
      onNotice(null)
      await refresh()
    },
    onError: (error) => {
      setDiscarding(false)
      onNotice({ tone: 'error', text: errorText(t, error) })
    },
  })

  return (
    <>
      <Summary draft={draft} content={content} />
      <CompanySection content={content} onChange={setContent} />
      <h2 className="mt-2 text-h4">{t('dictionary.tables.title')}</h2>
      {Object.entries(content.datasets).map(([key, table]) => (
        <TableCard
          key={key}
          tableKey={key}
          table={table}
          onChange={(next) =>
            setContent({ ...content, datasets: { ...content.datasets, [key]: next } })
          }
        />
      ))}
      <MetricsSection content={content} onChange={setContent} />
      <JoinsSection content={content} onChange={setContent} />
      <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center justify-between gap-3 border-t border-line bg-paper px-4 py-3 tablet:mx-0 tablet:rounded-panel tablet:border">
        <p className="m-0 text-small" aria-live="polite">
          {pending > 0
            ? t('dictionary.actions.toCheck', { count: pending })
            : dirty
              ? t('dictionary.actions.unsaved')
              : t('dictionary.summaryClear')}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => setDiscarding(true)}>
            {t('dictionary.actions.discard')}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={onRedraft}>
            {busy ? t('dictionary.actions.redrafting') : t('dictionary.actions.redraftAi')}
          </Button>
          <Button
            variant="secondary"
            disabled={!dirty || save.isPending}
            onClick={() => {
              onNotice(null)
              save.mutate()
            }}
          >
            {save.isPending ? t('dictionary.actions.saving') : t('dictionary.actions.save')}
          </Button>
          <Button disabled={pending > 0 || confirm.isPending} onClick={() => confirm.mutate()}>
            {confirm.isPending
              ? t('dictionary.actions.confirming')
              : t('dictionary.actions.confirm')}
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={discarding}
        onOpenChange={setDiscarding}
        title={t('dictionary.actions.discardTitle')}
        description={t('dictionary.actions.discardBody')}
        confirmLabel={t('dictionary.actions.discardConfirm')}
        busy={discard.isPending}
        onConfirm={() => discard.mutate()}
      />
    </>
  )
}
