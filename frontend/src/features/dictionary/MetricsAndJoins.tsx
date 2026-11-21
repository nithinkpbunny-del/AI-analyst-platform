import './strings'
import { Check, CircleAlert, CircleCheck, CircleHelp, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Select } from '../../components/ui/select'
import type { DictionaryContent, DictionaryMetric } from '../../lib/dictionaryApi'
import { metricErrorText } from './text'
import { duplicateMetrics, metricLabel } from './metricNames'

const METRIC_NAME = /^[a-z][a-z0-9_]{0,59}$/i

function MetricStatus({ metric }: { metric: DictionaryMetric }) {
  const { t } = useTranslation()
  if (metric.valid === true) {
    return (
      <span className="inline-flex items-center gap-1 text-caption text-gain">
        <CircleCheck className="size-4" aria-hidden /> {t('dictionary.metrics.works')}
      </span>
    )
  }
  if (metric.valid === false) {
    return (
      <span className="inline-flex items-center gap-1 text-caption text-loss">
        <CircleAlert className="size-4" aria-hidden /> {t('dictionary.metrics.broken')}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-caption text-ink-muted">
      <CircleHelp className="size-4" aria-hidden /> {t('dictionary.metrics.unchecked')}
    </span>
  )
}

export function MetricsSection({
  content,
  onChange,
}: {
  content: DictionaryContent
  onChange?: (next: DictionaryContent) => void
}) {
  const { t } = useTranslation()
  const editable = onChange !== undefined
  // Drafted metrics that repeat another one are folded away (M19): one name per number.
  const repeated = duplicateMetrics(content)
  const entries = Object.entries(content.metrics).filter(([key]) => !repeated[key])
  const folded = Object.keys(content.metrics).filter((key) => repeated[key])
  const tables = Object.entries(content.datasets)
  const [name, setName] = useState('')
  const [formula, setFormula] = useState('')
  const [table, setTable] = useState(tables[0]?.[0] ?? '')
  const nameProblem = !name
    ? null
    : !METRIC_NAME.test(name)
      ? t('dictionary.metrics.nameInvalid')
      : content.metrics[name]
        ? t('dictionary.metrics.nameTaken')
        : null

  const update = (key: string, patch: Partial<DictionaryMetric>) =>
    onChange?.({
      ...content,
      metrics: {
        ...content.metrics,
        [key]: {
          ...content.metrics[key]!,
          ...patch,
          valid: null,
          error: null,
          needs_review: false,
        },
      },
    })

  const add = (event: FormEvent) => {
    event.preventDefault()
    if (!name || nameProblem || !formula.trim()) return
    onChange?.({
      ...content,
      metrics: {
        ...content.metrics,
        [name]: {
          expr: formula.trim(),
          dataset: table || null,
          unit: null,
          good_direction: null,
          meaning: '',
          source: 'owner',
          valid: null,
          error: null,
          needs_review: false,
        },
      },
    })
    setName('')
    setFormula('')
  }

  return (
    <section
      aria-labelledby="dictionary-metrics"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="dictionary-metrics" className="text-h4">
        {t('dictionary.metrics.title')}
      </h2>
      <p className="m-0 mt-2 max-w-prose text-small text-ink-muted">
        {t('dictionary.metrics.intro')}
      </p>
      {entries.length === 0 ? (
        <p className="m-0 mt-4 text-small text-ink-muted">{t('dictionary.metrics.none')}</p>
      ) : (
        <ul className="m-0 mt-4 list-none p-0">
          {entries.map(([key, metric]) => {
            const error = metricErrorText(t, metric.error)
            return (
              <li
                key={key}
                className="flex flex-col gap-2 border-b border-line py-4 last:border-b-0"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="m-0 text-small font-medium">
                    {metricLabel(content, key)}
                    {metric.meaning ? (
                      <span className="font-normal text-ink-muted"> · {metric.meaning}</span>
                    ) : null}
                    <span className="block text-caption font-normal text-ink-muted">
                      {t('dictionary.metrics.key', { key })}
                    </span>
                  </p>
                  <div className="flex items-center gap-3">
                    <MetricStatus metric={metric} />
                    {editable ? (
                      <button
                        type="button"
                        aria-label={t('dictionary.metrics.remove', { name: key })}
                        className="inline-flex size-11 items-center justify-center rounded-control hover:bg-loss-soft"
                        onClick={() => {
                          const rest = { ...content.metrics }
                          delete rest[key]
                          onChange({ ...content, metrics: rest })
                        }}
                      >
                        <Trash2 className="size-4 text-loss" aria-hidden />
                      </button>
                    ) : null}
                  </div>
                </div>
                {editable ? (
                  <Input
                    value={metric.expr}
                    maxLength={400}
                    className="font-mono"
                    aria-label={`${t('dictionary.metrics.formula')}: ${key}`}
                    onChange={(event) => update(key, { expr: event.target.value })}
                  />
                ) : (
                  <code className="text-small">{metric.expr}</code>
                )}
                {metric.needs_review ? (
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-control border-l-4 border-l-caution bg-caution-soft px-3 py-2">
                    <p className="m-0 max-w-prose text-small text-caution">
                      {t('dictionary.metrics.needsCheck')}
                    </p>
                    {editable ? (
                      <button
                        type="button"
                        onClick={() =>
                          onChange({
                            ...content,
                            metrics: {
                              ...content.metrics,
                              [key]: { ...metric, needs_review: false },
                            },
                          })
                        }
                        aria-label={`${t('dictionary.column.check')}: ${key}`}
                        className="inline-flex min-h-11 items-center gap-2 rounded-control border border-line-strong bg-surface px-4 text-small font-medium hover:bg-neel-soft"
                      >
                        <Check className="size-4" aria-hidden />
                        {t('dictionary.column.check')}
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {error.text ? (
                  <p className="m-0 text-small text-loss">
                    {error.text}
                    {error.detail ? (
                      <span className="block text-caption text-ink-muted">
                        {t('dictionary.metrics.detail', { detail: error.detail })}
                      </span>
                    ) : null}
                  </p>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
      {folded.length > 0 ? (
        <details className="mt-4 border-t border-line pt-2">
          <summary className="flex min-h-11 cursor-pointer items-center text-small font-medium">
            {t('dictionary.metrics.repeatsTitle', { count: folded.length })}
          </summary>
          <p className="m-0 max-w-prose text-small text-ink-muted">
            {t('dictionary.metrics.repeatsIntro')}
          </p>
          <ul className="m-0 mt-2 list-none p-0">
            {folded.map((key) => (
              <li
                key={key}
                className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-line py-2 last:border-b-0"
              >
                <span className="text-small">
                  {t('dictionary.metrics.repeatOf', {
                    name: metricLabel(content, key),
                    key,
                    other: metricLabel(content, repeated[key]!),
                  })}
                </span>
                {editable ? (
                  <button
                    type="button"
                    aria-label={t('dictionary.metrics.remove', { name: key })}
                    className="inline-flex size-11 items-center justify-center rounded-control hover:bg-loss-soft"
                    onClick={() => {
                      const rest = { ...content.metrics }
                      delete rest[key]
                      onChange({ ...content, metrics: rest })
                    }}
                  >
                    <Trash2 className="size-4 text-loss" aria-hidden />
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {editable ? (
        <form
          onSubmit={add}
          className="mt-4 grid gap-3 border-t border-line pt-4 tablet:grid-cols-[1fr_2fr_1fr_auto]"
        >
          <h3 className="m-0 text-small font-medium tablet:col-span-full">
            {t('dictionary.metrics.addTitle')}
          </h3>
          <label className="flex flex-col gap-1.5 text-small">
            {t('dictionary.metrics.addName')}
            <Input
              value={name}
              maxLength={60}
              aria-invalid={nameProblem ? true : undefined}
              onChange={(event) => setName(event.target.value)}
            />
            {nameProblem ? <span className="text-caption text-loss">{nameProblem}</span> : null}
          </label>
          <label className="flex flex-col gap-1.5 text-small">
            {t('dictionary.metrics.addFormula')}
            <Input
              value={formula}
              maxLength={400}
              className="font-mono"
              onChange={(event) => setFormula(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1.5 text-small">
            {t('dictionary.metrics.addTable')}
            <Select value={table} onChange={(event) => setTable(event.target.value)}>
              <option value="">{t('dictionary.metrics.anyTable')}</option>
              {tables.map(([key, entry]) => (
                <option key={key} value={key}>
                  {entry.name}
                </option>
              ))}
            </Select>
          </label>
          <Button
            type="submit"
            variant="secondary"
            className="self-end"
            disabled={!name || !!nameProblem || !formula.trim()}
          >
            {t('dictionary.metrics.addButton')}
          </Button>
        </form>
      ) : null}
    </section>
  )
}

export function JoinsSection({
  content,
  onChange,
}: {
  content: DictionaryContent
  onChange?: (next: DictionaryContent) => void
}) {
  const { t } = useTranslation()
  const joins = content.relationships
  return (
    <section
      aria-labelledby="dictionary-joins"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="dictionary-joins" className="text-h4">
        {t('dictionary.joins.title')}
      </h2>
      <p className="m-0 mt-2 max-w-prose text-small text-ink-muted">
        {t('dictionary.joins.intro')}
      </p>
      {joins.length === 0 ? (
        <p className="m-0 mt-4 text-small text-ink-muted">{t('dictionary.joins.none')}</p>
      ) : (
        <ul className="m-0 mt-4 list-none p-0">
          {joins.map((join, index) => (
            <li
              key={`${join.left}-${join.right}`}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-3 last:border-b-0"
            >
              <div>
                <p className="m-0 text-small">
                  <span className="font-mono">{join.left}</span> →{' '}
                  <span className="font-mono">{join.right}</span>
                </p>
                <p className="m-0 text-caption text-ink-muted">
                  {t(`dictionary.joins.types.${join.type}`)}
                  {join.match_rate !== null
                    ? ` · ${t('dictionary.joins.match', { rate: Math.round(join.match_rate * 100) })}`
                    : ''}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {join.needs_review && onChange ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    aria-label={`${t('dictionary.column.check')}: ${join.left}`}
                    onClick={() =>
                      onChange({
                        ...content,
                        relationships: content.relationships.map((item, i) =>
                          i === index ? { ...item, needs_review: false } : item,
                        ),
                      })
                    }
                  >
                    {t('dictionary.column.check')}
                  </Button>
                ) : null}
                {onChange ? (
                  <button
                    type="button"
                    aria-label={t('dictionary.joins.remove', {
                      left: join.left,
                      right: join.right,
                    })}
                    className="inline-flex size-11 items-center justify-center rounded-control hover:bg-loss-soft"
                    onClick={() =>
                      onChange({
                        ...content,
                        relationships: content.relationships.filter((_, i) => i !== index),
                      })
                    }
                  >
                    <Trash2 className="size-4 text-loss" aria-hidden />
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
