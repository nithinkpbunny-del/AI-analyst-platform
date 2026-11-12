import { useMutation, useQuery } from '@tanstack/react-query'
import { SlidersHorizontal } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { ChartRenderer } from '../../components/ChartRenderer'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { Highlight } from '../../components/Highlight'
import { PageHeader } from '../../components/PageHeader'
import { QualityBanner } from '../../components/QualityBanner'
import { ScenarioCard } from '../../components/ScenarioCard'
import { Button } from '../../components/ui/button'
import { SelectField } from '../../components/ui/select'
import { ApiError } from '../../lib/api'
import type { ChartSpec } from '../../lib/chatApi'
import { formatRelativeTime } from '../../lib/format'
import { useCan } from '../../lib/permissions'
import {
  whatIfApi,
  type Lever,
  type WhatIfOptions,
  type WhatIfRequest,
  type WhatIfResult,
} from '../../lib/whatIfApi'
import { AssumptionDialog } from './AssumptionDialog'
import {
  assumptionText,
  confidenceReason,
  effectText,
  ELASTICITY_KEY,
  errorText,
  headline,
  LEVER_RANGE,
  leverValue,
  money,
  rangeText,
  scenarioTitle,
  signed,
  straddlesZero,
} from './text'

const OPTIONS = ['whatif-options'] as const
const HORIZONS = [1, 2, 3, 6]
const COLUMNS = [
  'bucket',
  'baseline',
  'baseline_low',
  'baseline_high',
  'scenario',
  'scenario_low',
  'scenario_high',
]

function monthLabel(bucket: string): string {
  const date = new Date(`${bucket}T00:00:00Z`)
  return date.toLocaleDateString('en-IN', { month: 'short', year: 'numeric', timeZone: 'UTC' })
}

interface MonthRow {
  month: string
  baseline: number
  scenario: number
  low: number
  high: number
}

function monthRows(result: WhatIfResult): MonthRow[] {
  const at = (name: string) => result.columns.indexOf(name)
  const index = Object.fromEntries(COLUMNS.map((name) => [name, at(name)]))
  return result.rows.map((row) => {
    const value = (name: string) => Number(row[index[name] ?? -1] ?? 0)
    return {
      month: monthLabel(String(row[index.bucket ?? 0])),
      baseline: value('baseline'),
      scenario: value('scenario'),
      low: value('scenario_low'),
      high: value('scenario_high'),
    }
  })
}

export function WhatIfPage() {
  const { t } = useTranslation()
  const canAsk = useCan('ask')

  useEffect(() => {
    document.title = `${t('whatIfPage.title')} · ${t('app.name')}`
  }, [t])

  const options = useQuery({
    queryKey: OPTIONS,
    queryFn: whatIfApi.options,
    enabled: canAsk,
    // An answer from the server (no data yet) is final; only a network failure is worth trying again.
    retry: (count, caught) => !(caught instanceof ApiError) && count < 2,
  })

  if (!canAsk) {
    return (
      <>
        <PageHeader title={t('whatIfPage.title')} intro={t('whatIfPage.intro')} />
        <EmptyState
          className="max-w-2xl"
          icon={SlidersHorizontal}
          message={t('whatIfPage.viewerNote')}
        />
      </>
    )
  }
  return (
    <>
      <PageHeader title={t('whatIfPage.title')} intro={t('whatIfPage.intro')} />
      {options.isError ? (
        options.error instanceof ApiError &&
        (options.error.code === 'no_dictionary' || options.error.code === 'no_sources') ? (
          <NoData />
        ) : (
          <FormAlert tone="error">{t('whatIfPage.loadFailed')}</FormAlert>
        )
      ) : options.data ? (
        options.data.metrics.length === 0 && !options.data.channels ? (
          <NoData message={t('whatIfPage.nothingToSimulate')} />
        ) : (
          <Simulator options={options.data} />
        )
      ) : null}
    </>
  )
}

function NoData({ message }: { message?: string }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      className="max-w-2xl"
      icon={SlidersHorizontal}
      message={message ?? t('whatIfPage.noData')}
      action={
        <Button asChild>
          <Link to="/connections">{t('whatIfPage.noDataAction')}</Link>
        </Button>
      }
    />
  )
}

function Simulator({ options }: { options: WhatIfOptions }) {
  const { t } = useTranslation()
  const levers = useMemo<Lever[]>(() => {
    const found = new Set<Lever>(options.metrics.flatMap((m) => m.levers))
    if (options.channels) found.add('marketing_spend')
    return (['price', 'discount', 'marketing_spend'] as const).filter((l) => found.has(l))
  }, [options])

  const [lever, setLever] = useState<Lever>(levers[0] ?? 'price')
  const metricsFor = options.metrics.filter((m) => m.levers.includes(lever))
  const [metricKey, setMetricKey] = useState(
    options.metrics.find((m) => m.key === 'revenue')?.key ?? options.metrics[0]?.key ?? '',
  )
  const metric = metricsFor.find((m) => m.key === metricKey) ?? metricsFor[0]
  const [dimension, setDimension] = useState('')
  const [scopeValue, setScopeValue] = useState('')
  const [channel, setChannel] = useState('')
  const [horizon, setHorizon] = useState(3)
  const [change, setChange] = useState(LEVER_RANGE[levers[0] ?? 'price'].start)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [result, setResult] = useState<WhatIfResult | null>(null)
  const [shown, setShown] = useState<WhatIfRequest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [ready, setReady] = useState('')
  const resultRef = useRef<HTMLDivElement>(null)

  const dimensionChoice = metric?.dimensions.find((d) => d.column === dimension)
  const scopeColumn = lever === 'marketing_spend' ? options.channels : dimensionChoice
  const scopeNeeds = lever === 'marketing_spend' ? true : Boolean(dimensionChoice)
  const values = useQuery({
    queryKey: ['whatif-values', scopeColumn?.table, scopeColumn?.column],
    queryFn: () => whatIfApi.values(scopeColumn?.table ?? '', scopeColumn?.column ?? ''),
    enabled: scopeNeeds && Boolean(scopeColumn),
    retry: false,
  })
  const chosenValue = lever === 'marketing_spend' ? channel : scopeValue

  const run = useMutation({
    mutationFn: (request: WhatIfRequest) => whatIfApi.run(request),
    onSuccess: (data, request) => {
      setResult(data)
      setShown(request)
      setError(null)
      setReady(t('whatIfPage.form.ready'))
    },
    onError: (caught) => {
      setError(errorText(t, caught))
      setReady('')
    },
  })

  function request(
    next: { change?: number; overrides?: Record<string, number> } = {},
  ): WhatIfRequest {
    const scope: Record<string, string> = {}
    if (lever === 'marketing_spend') scope.channel = channel
    else if (dimensionChoice && scopeValue) scope[dimensionChoice.column] = scopeValue
    const assumptions = next.overrides ?? overrides
    return {
      lever,
      change: next.change ?? change,
      ...(lever === 'marketing_spend' ? {} : { metric: metric?.key }),
      scope,
      horizon,
      ...(Object.keys(assumptions).length ? { assumptions } : {}),
    }
  }

  const blocked = lever === 'marketing_spend' ? !channel : Boolean(dimensionChoice) && !scopeValue
  function submit(event: FormEvent) {
    event.preventDefault()
    if (blocked || run.isPending) return
    setOverrides({})
    run.mutate(request({ overrides: {} }))
  }

  function chooseLever(next: Lever) {
    setLever(next)
    setChange(LEVER_RANGE[next].start)
    setOverrides({})
    setResult(null)
    setShown(null)
    setError(null)
    setReady('')
  }

  useEffect(() => {
    if (result && resultRef.current) {
      resultRef.current.scrollIntoView?.({ block: 'nearest' })
    }
  }, [result])

  const facts = result?.facts
  const range = LEVER_RANGE[lever]

  return (
    <div className="flex max-w-conversation flex-col gap-6">
      <form
        onSubmit={submit}
        noValidate
        aria-label={t('whatIfPage.form.title')}
        className="flex flex-col gap-4 rounded-panel border border-line bg-surface p-4 tablet:p-6"
      >
        <h2 className="m-0 text-h4">{t('whatIfPage.form.title')}</h2>
        <SelectField
          label={t('whatIfPage.form.lever')}
          value={lever}
          onChange={(event) => chooseLever(event.target.value as Lever)}
        >
          {levers.map((l) => (
            <option key={l} value={l}>
              {t(`whatIfPage.levers.${l}`)}
            </option>
          ))}
        </SelectField>
        {lever !== 'marketing_spend' && metricsFor.length > 1 ? (
          <SelectField
            label={t('whatIfPage.form.metric')}
            value={metric?.key ?? ''}
            onChange={(event) => {
              setMetricKey(event.target.value)
              setDimension('')
              setScopeValue('')
            }}
          >
            {metricsFor.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </SelectField>
        ) : null}
        {lever === 'marketing_spend' ? (
          <SelectField
            label={t('whatIfPage.form.channel')}
            value={channel}
            onChange={(event) => setChannel(event.target.value)}
          >
            <option value="">—</option>
            {(values.data ?? []).map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </SelectField>
        ) : (
          <div className="grid gap-4 tablet:grid-cols-2">
            <SelectField
              label={t('whatIfPage.form.scope')}
              value={dimension}
              onChange={(event) => {
                setDimension(event.target.value)
                setScopeValue('')
              }}
            >
              <option value="">{t('whatIfPage.form.scopeAll')}</option>
              {(metric?.dimensions ?? []).map((d) => (
                <option key={d.column} value={d.column}>
                  {d.label}
                </option>
              ))}
            </SelectField>
            {dimensionChoice ? (
              <SelectField
                label={t('whatIfPage.form.scopeValue')}
                value={scopeValue}
                onChange={(event) => setScopeValue(event.target.value)}
              >
                <option value="">—</option>
                {(values.data ?? []).map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </SelectField>
            ) : null}
          </div>
        )}
        <SelectField
          label={t('whatIfPage.form.horizon')}
          value={String(horizon)}
          onChange={(event) => setHorizon(Number(event.target.value))}
        >
          {HORIZONS.filter((h) => h <= options.max_horizon).map((h) => (
            <option key={h} value={h}>
              {t('whatIfPage.form.horizonMonths', { count: h })}
            </option>
          ))}
        </SelectField>
        <div>
          <label htmlFor="whatif-change" className="text-small font-medium">
            {t('whatIfPage.form.change')}
          </label>
          <div className="mt-1 flex items-center gap-3">
            <input
              id="whatif-change"
              type="number"
              inputMode="decimal"
              min={range.min}
              max={range.max}
              step={range.step}
              value={change}
              onChange={(event) => setChange(Number(event.target.value))}
              className="min-h-11 w-28 rounded-control border border-line-strong bg-surface px-3 text-small tabular"
            />
            <span className="text-small text-ink-muted">{leverValue(t, lever, change)}</span>
          </div>
        </div>
        {error ? <FormAlert tone="error">{error}</FormAlert> : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={blocked || run.isPending}>
            {run.isPending ? t('whatIfPage.form.running') : t('whatIfPage.form.run')}
          </Button>
          <p className="m-0 text-small text-ink-muted">
            {t('whatIfPage.form.words')}{' '}
            <Link to="/ask" className="text-neel underline">
              {t('whatIfPage.form.ask')}
            </Link>
          </p>
        </div>
        <p role="status" aria-live="polite" className="sr-only">
          {ready}
        </p>
      </form>

      {facts && shown && result ? (
        <div ref={resultRef} className="flex flex-col gap-4">
          <Result
            facts={facts}
            result={result}
            shown={shown}
            scopeText={chosenValue}
            leverRange={range}
            change={change}
            onChange={setChange}
            onCommit={(value) => {
              setChange(value)
              run.mutate(request({ change: value }))
            }}
            onEdit={() => setEditing(true)}
          />
          {facts.assumptions.find((a) => a.key === ELASTICITY_KEY[lever]) ? (
            <AssumptionDialog
              key={`${editing}`}
              lever={lever}
              current={Number(
                facts.assumptions.find((a) => a.key === ELASTICITY_KEY[lever])?.value ?? 0,
              )}
              overridden={ELASTICITY_KEY[lever] in overrides}
              open={editing}
              onOpenChange={setEditing}
              onApply={(value) => {
                const next = { ...overrides, [ELASTICITY_KEY[lever]]: value }
                setOverrides(next)
                setEditing(false)
                run.mutate(request({ overrides: next }))
              }}
              onReset={() => {
                setOverrides({})
                setEditing(false)
                run.mutate(request({ overrides: {} }))
              }}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function Result({
  facts,
  result,
  shown,
  scopeText,
  leverRange,
  change,
  onChange,
  onCommit,
  onEdit,
}: {
  facts: WhatIfResult['facts']
  result: WhatIfResult
  shown: WhatIfRequest
  scopeText: string
  leverRange: (typeof LEVER_RANGE)[Lever]
  change: number
  onChange: (value: number) => void
  onCommit: (value: number) => void
  onEdit: () => void
}) {
  const { t } = useTranslation()
  const rows = monthRows(result)
  const { lead, figure } = headline(t, facts)
  const unit = facts.unit
  const lever = shown.lever
  const defaulted = result.notes.includes('what_if_default_assumption')
  const oldest = result.tables
    .map((table) => table.fetched_at)
    .filter((at): at is string => Boolean(at))
    .sort()[0]
  const tables = [...new Set(result.tables.map((table) => table.name ?? table.table))].join(', ')
  const direction =
    Math.abs(facts.delta_pct_expected) < 0.5 ? 'same' : facts.delta_expected > 0 ? 'up' : 'down'
  const chart: ChartSpec = {
    chart_id: 'whatif',
    type: 'line',
    title: t('whatIfPage.chart.title', {
      metric: (facts.channel ? `${facts.channel} revenue` : facts.metric).replace(/_/g, ' '),
      direction: t(`whatIfPage.chart.${direction}`),
    }),
    x: { name: t('whatIfPage.table.month'), values: rows.map((row) => row.month) },
    series: [
      { name: t('whatIfPage.chart.without'), data: rows.map((row) => Math.round(row.baseline)) },
      { name: t('whatIfPage.chart.with'), data: rows.map((row) => Math.round(row.scenario)) },
    ],
    result_id: '',
    unit,
  }
  const edit = facts.assumptions.find((a) => a.key === ELASTICITY_KEY[lever])
  const sideEffects = facts.side_effects.filter(
    (effect) => !(effect.code === 'units_change' && lever === 'marketing_spend'),
  )

  return (
    <>
      <section
        aria-label={t('whatIfPage.title')}
        className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
      >
        <p className="m-0 font-display text-h3">
          {lead} <Highlight>{figure}</Highlight>
        </p>
        <p className="m-0 mt-2 text-small text-ink-muted tabular">{rangeText(t, facts)}</p>
        {straddlesZero(facts) ? (
          <p className="m-0 mt-1 text-small text-caution">{t('whatIfPage.headline.straddles')}</p>
        ) : null}
        <p className="m-0 mt-1 text-small text-ink-muted tabular">
          {t('whatIfPage.headline.of', { baseline: money(facts.baseline_total, unit) })}
        </p>
      </section>

      {defaulted ? (
        <QualityBanner>{t('ask.notes.what_if_default_assumption')}</QualityBanner>
      ) : null}
      {result.quality_warnings.length > 0 ? (
        <QualityBanner>{t('whatIfPage.quality')}</QualityBanner>
      ) : null}

      <ScenarioCard
        title={scenarioTitle(t, shown, scopeText)}
        horizon={t('whatIfPage.headline.months', { count: facts.horizon_months })}
        lever={{
          label: t(`whatIfPage.changeLabel.${lever}`),
          value: change,
          min: leverRange.min,
          max: leverRange.max,
          step: leverRange.step,
          format: (value) => leverValue(t, lever, value),
        }}
        onLeverChange={onChange}
        onLeverCommit={onCommit}
        baseline={facts.baseline_total}
        scenario={{
          expected: facts.scenario_total,
          low: facts.scenario_low,
          high: facts.scenario_high,
        }}
        formatValue={(value) => money(value, unit)}
        assumptions={facts.assumptions
          .map((item) => assumptionText(t, facts, item))
          .filter((text) => text !== '')}
        onEditAssumptions={edit ? onEdit : undefined}
        confidence={{
          level: facts.confidence.level,
          reason: confidenceReason(t, facts.confidence.reason),
        }}
      />

      <section className="rounded-panel border border-line bg-surface p-4 tablet:p-6">
        <ChartRenderer spec={chart} />
      </section>

      {sideEffects.length > 0 || facts.net_after_spend !== undefined ? (
        <section
          aria-labelledby="whatif-effects"
          className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
        >
          <h3 id="whatif-effects" className="m-0 text-h4">
            {t('whatIfPage.effects.title')}
          </h3>
          <ul className="m-0 mt-2 list-disc space-y-1 pl-5 text-small">
            {sideEffects.map((effect) => (
              <li key={effect.code}>{effectText(t, effect, unit)}</li>
            ))}
            {facts.net_after_spend !== undefined ? (
              <li>
                {t('whatIfPage.effects.netAfterSpend', {
                  amount: signed(facts.net_after_spend, 'INR'),
                })}
              </li>
            ) : null}
          </ul>
        </section>
      ) : null}

      <section
        aria-labelledby="whatif-months"
        className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
      >
        <h3 id="whatif-months" className="m-0 text-h4">
          {t('whatIfPage.table.title')}
        </h3>
        <div
          className="mt-2 overflow-x-auto"
          tabIndex={0}
          role="region"
          aria-labelledby="whatif-months"
        >
          <table className="w-full min-w-[480px] border-collapse text-small tabular">
            <thead>
              <tr className="border-b border-line text-left text-ink-muted">
                <th className="py-2 pr-4 font-medium">{t('whatIfPage.table.month')}</th>
                <th className="py-2 pr-4 text-right font-medium">
                  {t('whatIfPage.table.without')}
                </th>
                <th className="py-2 pr-4 text-right font-medium">{t('whatIfPage.table.with')}</th>
                <th className="py-2 text-right font-medium">{t('whatIfPage.table.range')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.month} className="min-h-11 border-b border-line">
                  <td className="py-3 pr-4">{row.month}</td>
                  <td className="py-3 pr-4 text-right">{money(row.baseline, unit)}</td>
                  <td className="py-3 pr-4 text-right">{money(row.scenario, unit)}</td>
                  <td className="py-3 text-right">
                    {money(row.low, unit)} – {money(row.high, unit)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {oldest ? (
          <p className="m-0 mt-3 text-caption text-ink-muted">
            {t('whatIfPage.source', { tables, when: formatRelativeTime(oldest) })}
          </p>
        ) : null}
      </section>
    </>
  )
}
