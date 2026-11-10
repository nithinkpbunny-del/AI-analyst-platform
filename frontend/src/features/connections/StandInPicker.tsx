import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'
import { Dialog, DialogClose, DialogContent } from '../../components/ui/dialog'
import { CheckboxField } from '../../components/ui/input'
import type { PickerConfig, Spreadsheet } from '../../lib/connectionsApi'

export interface Pending {
  config: PickerConfig
  resolve: (picked: Spreadsheet[]) => void
}

/** The development stand-in for the Google Picker: the demo spreadsheets as a checklist. */
export function StandInPicker({
  pending,
  onDone,
}: {
  pending: Pending | null
  onDone: (picked: Spreadsheet[]) => void
}) {
  const { t } = useTranslation()
  const files = pending?.config.stand_in_files ?? []
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const toggle = (id: string, on: boolean) =>
    setChosen((current) => {
      const next = new Set(current)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  return (
    <Dialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) onDone([])
      }}
    >
      <DialogContent
        title={t('connections.google.standInTitle')}
        description={t('connections.google.standInNote')}
      >
        <fieldset className="m-0 mt-4 flex flex-col gap-3 border-0 p-0">
          <legend className="sr-only">{t('connections.google.standInTitle')}</legend>
          {files.map((file) => (
            <CheckboxField
              key={file.id}
              name={file.id}
              label={file.name}
              checked={chosen.has(file.id)}
              onChange={(on) => toggle(file.id, on)}
            />
          ))}
        </fieldset>
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <DialogClose asChild>
            <Button variant="secondary">{t('dialog.cancel')}</Button>
          </DialogClose>
          <Button
            disabled={chosen.size === 0}
            onClick={() => {
              onDone(files.filter((file) => chosen.has(file.id)))
              setChosen(new Set())
            }}
          >
            {t('connections.google.standInConfirm')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
