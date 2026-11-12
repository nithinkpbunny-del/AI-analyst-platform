import { Pencil } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '../lib/cn'
import { formatINR } from '../lib/format'
import { Button } from './ui/button'
import { Slider } from './ui/slider'

export type Confidence = 'high' | 'medium' | 'low'

export interface ScenarioCardProps {
  title: string
  /** e.g. "next 3 months" */
  horizon: string
  lever: {
    label: string
    value: number
    min: number
    max: number
    step: number
    /** How to show the lever value, e.g. (v) => `${v}% discount` */
    format: (value: number) => string
  }
  onLeverChange: (value: number) => void
  /** The slider was released (or a key press ended): the moment to work the scenario out again. */
  onLeverCommit?: (value: number) => void
  baseline: number
  scenario: { expected: number; low: number; high: number }
  /** Amounts are rupees unless a formatter is given. */
  formatValue?: (value: number) => string
  assumptions: string[]
  onEditAssumptions?: () => void
  confidence: { level: Confidence; reason: string }
  className?: string
}

const confidenceStyle: Record<Confidence, string> = {
  high: 'bg-gain-soft text-gain',
  medium: 'bg-caution-soft text-caution',
  low: 'bg-loss-soft text-loss',
}

/** What-if result: lever slider, baseline vs scenario bars with low-high whisker, assumptions, confidence. */
export function ScenarioCard({
  title,
  horizon,
  lever,
  onLeverChange,
  onLeverCommit,
  baseline,
  scenario,
  formatValue = formatINR,
  assumptions,
  onEditAssumptions,
  confidence,
  className,
}: ScenarioCardProps) {
  const { t } = useTranslation()
  const scaleMax = Math.max(baseline, scenario.high) * 1.08 || 1
  const pct = (v: number) => `${Math.max(0, (v / scaleMax) * 100)}%`

  return (
    <section
      aria-label={title}
      className={cn('rounded-panel border border-line bg-surface p-4 tablet:p-6', className)}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-h4">{title}</h3>
          <p className="m-0 mt-1 text-small text-ink-muted">
            {t('scenarioCard.projection', { horizon })}
          </p>
        </div>
        <span
          className={cn(
            'rounded-pill px-3 py-1 text-caption font-semibold',
            confidenceStyle[confidence.level],
          )}
        >
          {t(`scenarioCard.confidence_${confidence.level}`)}
        </span>
      </div>

      <div className="mt-4">
        <div className="flex items-baseline justify-between gap-3 text-small">
          <span className="font-medium">{lever.label}</span>
          <span className="tabular text-ink-muted">{lever.format(lever.value)}</span>
        </div>
        <Slider
          aria-label={lever.label}
          value={[lever.value]}
          min={lever.min}
          max={lever.max}
          step={lever.step}
          onValueChange={(values) => {
            const next = values[0]
            if (next !== undefined) onLeverChange(next)
          }}
          onValueCommit={(values) => {
            const next = values[0]
            if (next !== undefined) onLeverCommit?.(next)
          }}
        />
      </div>

      <dl className="mt-2 space-y-4">
        <div>
          <dt className="text-small text-ink-muted">{t('scenarioCard.without')}</dt>
          <dd className="m-0 mt-1 flex items-center gap-3">
            <span className="relative h-3 flex-1 rounded-sheet bg-paper">
              <span
                className="absolute inset-y-0 left-0 rounded-sheet bg-line-strong"
                style={{ width: pct(baseline) }}
              />
            </span>
            <span className="w-24 text-right font-semibold tabular">{formatValue(baseline)}</span>
          </dd>
        </div>
        <div>
          <dt className="text-small text-ink-muted">{t('scenarioCard.with')}</dt>
          <dd className="m-0 mt-1 flex items-center gap-3">
            <span className="relative h-3 flex-1 rounded-sheet bg-paper" data-testid="scenario-bar">
              <span
                className="absolute inset-y-0 left-0 rounded-sheet bg-neel"
                style={{ width: pct(scenario.expected) }}
              />
              <span
                aria-hidden
                data-testid="range-whisker"
                className="absolute top-1/2 h-5 -translate-y-1/2 border-x-2 border-ink"
                style={{
                  left: pct(scenario.low),
                  width: `calc(${pct(scenario.high)} - ${pct(scenario.low)})`,
                }}
              >
                <span className="absolute top-1/2 h-0.5 w-full -translate-y-1/2 bg-ink" />
              </span>
            </span>
            <span className="w-24 text-right font-semibold tabular">
              {formatValue(scenario.expected)}
            </span>
          </dd>
          <dd className="m-0 mt-1 text-caption text-ink-muted tabular">
            {t('scenarioCard.between', {
              low: formatValue(scenario.low),
              high: formatValue(scenario.high),
            })}
          </dd>
        </div>
      </dl>

      <div className="mt-4 border-t border-line pt-4">
        <div className="flex items-center justify-between gap-3">
          <h4 className="font-sans text-small font-semibold tracking-normal">
            {t('scenarioCard.assumptions')}
          </h4>
          {onEditAssumptions ? (
            <Button variant="ghost" size="sm" onClick={onEditAssumptions}>
              <Pencil aria-hidden />
              {t('scenarioCard.editAssumptions')}
            </Button>
          ) : null}
        </div>
        <ul className="m-0 mt-2 list-disc space-y-1 pl-5 text-small">
          {assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        <p className="m-0 mt-3 text-caption text-ink-muted">{confidence.reason}</p>
      </div>
    </section>
  )
}
