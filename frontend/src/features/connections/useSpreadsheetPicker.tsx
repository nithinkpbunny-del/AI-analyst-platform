import { useCallback, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { PickerConfig, Spreadsheet } from '../../lib/connectionsApi'
import { pickSpreadsheets } from '../../lib/googlePicker'
import { StandInPicker, type Pending } from './StandInPicker'

/**
 * `pick(config)` opens the Google Picker (or, in development, a stand-in list) and resolves with the chosen
 * spreadsheets, or an empty list when cancelled. Render `element` once in the page.
 */
export function useSpreadsheetPicker(): {
  pick: (config: PickerConfig) => Promise<Spreadsheet[]>
  element: ReactNode
} {
  const { t } = useTranslation()
  const [pending, setPending] = useState<Pending | null>(null)
  const pendingRef = useRef<Pending | null>(null)
  const pick = useCallback(
    (config: PickerConfig) => {
      if (!config.stand_in) return pickSpreadsheets(config, t('connections.google.pickerTitle'))
      return new Promise<Spreadsheet[]>((resolve) => {
        pendingRef.current = { config, resolve }
        setPending(pendingRef.current)
      })
    },
    [t],
  )
  const done = (picked: Spreadsheet[]) => {
    pendingRef.current?.resolve(picked)
    pendingRef.current = null
    setPending(null)
  }
  return { pick, element: <StandInPicker pending={pending} onDone={done} /> }
}
