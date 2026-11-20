import './strings'
import type { TFunction } from 'i18next'
import type { TallyPc } from '../../lib/connectionsApi'
import { formatRelativeTime } from '../../lib/format'

/** "Connected", "Tally PC offline since 2 h ago", "Tally is not open on ACCOUNTS-PC" ... */
export function tallyStateText(t: TFunction, tally: TallyPc, now?: Date): string {
  const pc = tally.device_name ?? ''
  if (tally.state === 'offline') {
    return tally.last_seen_at
      ? t('connections.tally.state.offline', { when: formatRelativeTime(tally.last_seen_at, now) })
      : t('connections.tally.state.offlineNever')
  }
  return t(`connections.tally.state.${tally.state}`, { pc })
}
