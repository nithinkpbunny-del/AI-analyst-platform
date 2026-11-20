import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { CheckboxField, Field } from '../../components/ui/input'
import { SelectField } from '../../components/ui/select'
import {
  dashboardApi,
  REFRESH_CHOICES,
  type RefreshMinutes,
  type Tile,
  type TileDates,
} from '../../lib/goalsApi'
import { addStrings } from '../../lib/i18n'
import dashboardStrings from '../../locales/en/dashboard'
import { DASHBOARD } from './keys'

addStrings(dashboardStrings)

function DatesChoice({ value, onChange }: { value: TileDates; onChange: (v: TileDates) => void }) {
  const { t } = useTranslation()
  return (
    <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
      <legend className="mb-1 text-small font-medium text-ink">{t('dashboard.dates.label')}</legend>
      {(['rolling', 'fixed'] as const).map((option) => (
        <div
          key={option}
          className="flex min-h-11 items-start gap-3 rounded-control px-1 py-2 hover:bg-neel-soft"
        >
          <input
            id={`tile-dates-${option}`}
            type="radio"
            name="tile-dates"
            value={option}
            checked={value === option}
            onChange={() => onChange(option)}
            aria-describedby={`tile-dates-${option}-hint`}
            className="mt-1 size-5 accent-neel"
          />
          <div>
            <label
              htmlFor={`tile-dates-${option}`}
              className="block cursor-pointer text-small text-ink"
            >
              {t(`dashboard.dates.${option}`)}
            </label>
            <p id={`tile-dates-${option}-hint`} className="m-0 text-caption text-ink-muted">
              {t(`dashboard.dates.${option}Hint`)}
            </p>
          </div>
        </div>
      ))}
    </fieldset>
  )
}

/** Pin an answer (BUILD_SPEC X8): it becomes a tile that re-runs the answer's figures, never the AI. */
export function PinDialog({
  messageId,
  question,
  open,
  onOpenChange,
}: {
  messageId: string
  question: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState(question.slice(0, 200))
  const [dates, setDates] = useState<TileDates>('rolling')
  const pin = useMutation({
    mutationFn: () => dashboardApi.pin(messageId, title.trim() || null, dates),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: DASHBOARD }),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    pin.mutate()
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t('dashboard.pin.title')} description={t('dashboard.pin.intro')}>
        {pin.isSuccess ? (
          <div className="mt-4 flex flex-col gap-4">
            <FormAlert tone="success">{t('dashboard.pin.done')}</FormAlert>
            <div className="flex justify-end">
              <Button asChild>
                <Link to="/dashboard">{t('dashboard.pin.open')}</Link>
              </Button>
            </div>
          </div>
        ) : (
          <form className="mt-4 flex flex-col gap-4" onSubmit={submit}>
            {pin.isError ? <FormAlert tone="error">{pin.error.message}</FormAlert> : null}
            <Field
              label={t('dashboard.pin.name')}
              value={title}
              maxLength={200}
              onChange={(event) => setTitle(event.target.value)}
            />
            <DatesChoice value={dates} onChange={setDates} />
            <div className="flex justify-end">
              <Button type="submit" disabled={pin.isPending}>
                {pin.isPending ? t('dashboard.pin.pinning') : t('dashboard.pin.submit')}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Rename a tile, how often it refreshes, its dates, and whether the team sees it. */
export function TileSettingsDialog({
  tile,
  open,
  onOpenChange,
  onSaved,
}: {
  tile: Tile
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (text: string) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState(tile.title)
  const [refresh, setRefresh] = useState<RefreshMinutes>(
    (REFRESH_CHOICES as readonly number[]).includes(tile.refresh_minutes)
      ? (tile.refresh_minutes as RefreshMinutes)
      : 60,
  )
  const [dates, setDates] = useState<TileDates>(tile.dates)
  const [shared, setShared] = useState(tile.shared)
  const [error, setError] = useState<string | undefined>()
  const save = useMutation({
    mutationFn: () =>
      dashboardApi.update(tile.id, {
        title: title.trim(),
        refresh_minutes: refresh,
        dates,
        shared,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: DASHBOARD })
      onSaved(t('dashboard.settings.saved'))
      onOpenChange(false)
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!title.trim()) {
      setError(t('dashboard.settings.nameError'))
      return
    }
    setError(undefined)
    save.mutate()
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t('dashboard.settings.title')}>
        <form className="mt-4 flex flex-col gap-4" onSubmit={submit} noValidate>
          {save.isError ? <FormAlert tone="error">{save.error.message}</FormAlert> : null}
          <Field
            label={t('dashboard.settings.name')}
            value={title}
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
            error={error}
          />
          <SelectField
            label={t('dashboard.settings.refresh')}
            value={String(refresh)}
            onChange={(event) => setRefresh(Number(event.target.value) as RefreshMinutes)}
          >
            {REFRESH_CHOICES.map((minutes) => (
              <option key={minutes} value={minutes}>
                {t(`dashboard.settings.refreshChoices.${minutes}`)}
              </option>
            ))}
          </SelectField>
          <DatesChoice value={dates} onChange={setDates} />
          <div className="flex flex-col gap-1">
            <CheckboxField
              name="shared"
              label={t('dashboard.settings.share')}
              checked={shared}
              onChange={setShared}
            />
            <p className="m-0 pl-8 text-caption text-ink-muted">
              {t('dashboard.settings.shareHint')}
            </p>
          </div>
          <div className="flex justify-end">
            <Button type="submit" disabled={save.isPending}>
              {t('dashboard.settings.save')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
