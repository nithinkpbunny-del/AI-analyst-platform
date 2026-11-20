import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { SelectField } from '../../components/ui/select'
import {
  goalsApi,
  type Goal,
  type GoalDirection,
  type GoalInput,
  type GoalOptions,
} from '../../lib/goalsApi'
import { PRESETS, presetPeriod, type Preset } from './periods'

/** Set a goal, or change one (BUILD_SPEC X7, 11 "Goals: create/edit"). The metric of a goal cannot change. */
export function GoalDialog({
  open,
  onOpenChange,
  options,
  goal,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  options: GoalOptions
  goal?: Goal
  onSaved: (text: string) => void
}) {
  const { t } = useTranslation()
  const metrics = options.metrics
  const [metric, setMetric] = useState(goal?.metric ?? metrics[0]?.key ?? '')
  const chosen = metrics.find((m) => m.key === metric)
  const [direction, setDirection] = useState<GoalDirection>(
    goal?.direction ?? chosen?.direction ?? 'at_least',
  )
  const [target, setTarget] = useState(goal ? String(goal.target) : '')
  const [preset, setPreset] = useState<Preset>(goal ? 'custom' : 'this_month')
  const initial = goal
    ? { start: goal.period_start, end: goal.period_end }
    : presetPeriod('this_month', options.today)
  const [start, setStart] = useState(initial.start)
  const [end, setEnd] = useState(initial.end)
  const [name, setName] = useState(goal?.name ?? '')
  const firstFilter = Object.entries(goal?.filters ?? {})[0]
  const [column, setColumn] = useState(firstFilter?.[0] ?? '')
  const [value, setValue] = useState(firstFilter?.[1] ?? '')
  const [errors, setErrors] = useState<{ target?: string; dates?: string; value?: string }>({})

  const save = useMutation({
    mutationFn: (input: GoalInput) =>
      goal
        ? goalsApi.update(goal.id, {
            target: input.target,
            period_start: input.period_start,
            period_end: input.period_end,
            direction: input.direction,
            name: input.name,
            filters: input.filters,
          })
        : goalsApi.create(input),
    onSuccess: () => {
      onSaved(goal ? t('goals.form.updated') : t('goals.form.saved'))
      onOpenChange(false)
    },
  })

  const choosePreset = (next: Preset) => {
    setPreset(next)
    if (next !== 'custom') {
      const period = presetPeriod(next, options.today)
      setStart(period.start)
      setEnd(period.end)
    }
  }

  const chooseMetric = (key: string) => {
    setMetric(key)
    setColumn('')
    const found = metrics.find((m) => m.key === key)
    if (found) setDirection(found.direction)
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const amount = Number(target.replaceAll(',', ''))
    const found: typeof errors = {}
    if (!Number.isFinite(amount) || amount <= 0) found.target = t('goals.form.errors.target')
    if (!start || !end || end < start) found.dates = t('goals.form.errors.dates')
    if (column && !value.trim()) found.value = t('goals.form.errors.filterValue')
    setErrors(found)
    if (Object.keys(found).length > 0) return
    save.mutate({
      metric,
      target: amount,
      period_start: start,
      period_end: end,
      direction,
      name: name.trim() || null,
      filters: column ? { [column]: value.trim() } : {},
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={goal ? t('goals.form.titleEdit') : t('goals.form.titleNew')}
        description={t('goals.form.intro')}
      >
        {metrics.length === 0 ? (
          <p className="m-0 mt-4 text-body text-ink-muted">{t('goals.form.noMetrics')}</p>
        ) : (
          <form className="mt-4 flex flex-col gap-4" onSubmit={submit} noValidate>
            {save.isError ? <FormAlert tone="error">{save.error.message}</FormAlert> : null}
            <SelectField
              label={t('goals.form.metric')}
              value={metric}
              disabled={Boolean(goal)}
              onChange={(event) => chooseMetric(event.target.value)}
            >
              {metrics.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </SelectField>
            <Field
              label={t('goals.form.target')}
              inputMode="decimal"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              hint={
                chosen?.unit === 'INR'
                  ? t('goals.form.targetHintInr')
                  : t('goals.form.targetHintCount')
              }
              error={errors.target}
            />
            <SelectField
              label={t('goals.form.direction')}
              value={direction}
              onChange={(event) => setDirection(event.target.value as GoalDirection)}
            >
              <option value="at_least">{t('goals.form.directions.at_least')}</option>
              <option value="at_most">{t('goals.form.directions.at_most')}</option>
            </SelectField>
            <SelectField
              label={t('goals.form.period')}
              value={preset}
              onChange={(event) => choosePreset(event.target.value as Preset)}
            >
              {PRESETS.map((p) => (
                <option key={p} value={p}>
                  {t(`goals.form.periods.${p}`)}
                </option>
              ))}
            </SelectField>
            <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
              <Field
                label={t('goals.form.start')}
                type="date"
                value={start}
                onChange={(event) => {
                  setPreset('custom')
                  setStart(event.target.value)
                }}
                error={errors.dates}
              />
              <Field
                label={t('goals.form.end')}
                type="date"
                value={end}
                onChange={(event) => {
                  setPreset('custom')
                  setEnd(event.target.value)
                }}
              />
            </div>
            <Field
              label={t('goals.form.name')}
              placeholder={t('goals.form.namePlaceholder')}
              value={name}
              maxLength={200}
              onChange={(event) => setName(event.target.value)}
            />
            {chosen && chosen.dimensions.length > 0 ? (
              <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
                <SelectField
                  label={t('goals.form.filter')}
                  value={column}
                  onChange={(event) => setColumn(event.target.value)}
                >
                  <option value="">{t('goals.form.filterNone')}</option>
                  {chosen.dimensions.map((d) => (
                    <option key={d.key} value={d.key}>
                      {d.label}
                    </option>
                  ))}
                </SelectField>
                {column ? (
                  <Field
                    label={t('goals.form.filterValue')}
                    placeholder={t('goals.form.filterValuePlaceholder')}
                    value={value}
                    maxLength={200}
                    onChange={(event) => setValue(event.target.value)}
                    error={errors.value}
                  />
                ) : null}
              </div>
            ) : null}
            <div className="flex justify-end">
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? t('goals.form.saving') : t('goals.form.save')}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
