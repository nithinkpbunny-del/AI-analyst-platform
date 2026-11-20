import './strings'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { connectionsApi, type TallyPc } from '../../lib/connectionsApi'
import { formatRelativeTime } from '../../lib/format'
import { CONNECTIONS } from './keys'
import { tallyStateText } from './tallyText'

/** The Tally PC's state, last contact and last data, and what to do about a problem (M16). */
export function TallyLines({ tally, now }: { tally: TallyPc; now?: Date }) {
  const { t } = useTranslation()
  const lines: string[] = []
  if (tally.device_name) {
    lines.push(
      t('connections.tally.pcLine', {
        pc: tally.device_name,
        version: tally.bridge_version ?? '?',
      }),
    )
  }
  if (tally.company) lines.push(t('connections.tally.companyLine', { company: tally.company }))
  if (tally.last_seen_at) {
    lines.push(
      t('connections.tally.lastSeen', { when: formatRelativeTime(tally.last_seen_at, now) }),
    )
  }
  lines.push(
    tally.last_sync_at
      ? t('connections.tally.lastSync', { when: formatRelativeTime(tally.last_sync_at, now) }) +
          (tally.last_full_sync_at
            ? ` (${t('connections.tally.lastFullSync', { when: formatRelativeTime(tally.last_full_sync_at, now) })})`
            : '')
      : t('connections.tally.noSyncYet'),
  )
  const problem = tally.problem
    ? t(`connections.tally.problems.${tally.problem}`, { defaultValue: '' })
    : ''
  return (
    <div className="mt-2 flex flex-col gap-1">
      <p className="m-0 text-small font-semibold" data-testid="tally-state">
        {tallyStateText(t, tally, now)}
      </p>
      <p className="m-0 text-small text-ink-muted">{lines.join(' · ')}</p>
      {tally.refresh_pending ? (
        <p className="m-0 text-small text-ink-muted">{t('connections.tally.refreshWaiting')}</p>
      ) : null}
      {problem && tally.state !== 'not_paired' ? (
        <p className="m-0 text-small text-caution">{problem}</p>
      ) : null}
      <p className="m-0 text-small text-ink-muted">{t('connections.tally.readOnlyNote')}</p>
    </div>
  )
}

/** Owners: unpair the PC (its token stops working at once), after a confirmation. */
export function UnpairTally({
  connectionId,
  pcName,
  onDone,
  onError,
}: {
  connectionId: string
  pcName: string
  onDone: (text: string) => void
  onError: (error: unknown) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const unpair = useMutation({
    mutationFn: () => connectionsApi.tallyUnpair(connectionId),
    onSuccess: () => {
      setOpen(false)
      void client.invalidateQueries({ queryKey: CONNECTIONS })
      onDone(t('connections.tally.unpaired'))
    },
    onError: (error) => {
      setOpen(false)
      onError(error)
    },
  })
  return (
    <>
      <Button variant="ghost" onClick={() => setOpen(true)}>
        {t('connections.tally.unpair')}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t('connections.tally.unpairTitle', { name: pcName })}
        description={t('connections.tally.unpairBody')}
        confirmLabel={t('connections.tally.unpairConfirm')}
        onConfirm={() => unpair.mutate()}
        busy={unpair.isPending}
      />
    </>
  )
}
