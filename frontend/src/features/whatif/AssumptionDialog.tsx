import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'
import { Dialog, DialogClose, DialogContent } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import type { Lever } from '../../lib/whatIfApi'
import { ELASTICITY_KEY } from './text'

/** Edit the one number the scenario rests on. Price and discount responses are negative, spend's is positive. */
export function AssumptionDialog({
  lever,
  current,
  overridden,
  open,
  onOpenChange,
  onApply,
  onReset,
}: {
  lever: Lever
  current: number
  overridden: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  onApply: (value: number) => void
  onReset: () => void
}) {
  const { t } = useTranslation()
  const key = ELASTICITY_KEY[lever]
  const positive = lever === 'marketing_spend'
  const [text, setText] = useState(String(Math.round(current * 100) / 100))
  const value = Number(text.replace(',', '.').replace('−', '-'))
  const valid =
    text.trim() !== '' &&
    Number.isFinite(value) &&
    (positive ? value >= 0 && value <= 10 : value <= 0 && value >= -10)

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) onApply(value)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t('whatIfPage.edit.title')} description={t('whatIfPage.edit.intro')}>
        <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
          <Field
            label={t(`whatIfPage.edit.${key}`)}
            inputMode="decimal"
            value={text}
            onChange={(event) => setText(event.target.value)}
            error={
              text.trim() !== '' && !valid
                ? t(
                    positive
                      ? 'whatIfPage.edit.invalidPositive'
                      : 'whatIfPage.edit.invalidNegative',
                  )
                : undefined
            }
          />
          <div className="flex flex-wrap justify-end gap-2">
            {overridden ? (
              <Button type="button" variant="ghost" onClick={onReset}>
                {t('whatIfPage.edit.reset')}
              </Button>
            ) : null}
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                {t('dialog.cancel')}
              </Button>
            </DialogClose>
            <Button type="submit" disabled={!valid}>
              {t('whatIfPage.edit.apply')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
