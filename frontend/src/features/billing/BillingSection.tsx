import { useMutation, useQuery } from '@tanstack/react-query'
import { lazy, Suspense, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { SelectField } from '../../components/ui/select'
import { formatMonth, formatUsd, type AiPlan } from '../../lib/aiApi'
import { saveFile } from '../../lib/api'
import {
  billingApi,
  type Billing,
  type BillingEvent,
  type Limit,
  type SummaryLine,
} from '../../lib/billingApi'
import { cn } from '../../lib/cn'
import { formatDate, formatNumber } from '../../lib/format'
import './strings'

// The plan change dialog loads when someone opens it.
const ChangePlanDialog = lazy(() => import('./ChangePlanDialog'))

const MONTHS_SHOWN = 6

/** The last months, newest first, from the current "YYYY-MM". */
function recentMonths(current: string): string[] {
  const [year = 1970, number = 1] = current.split('-').map(Number)
  return Array.from({ length: MONTHS_SHOWN }, (_, back) => {
    const index = year * 12 + (number - 1) - back
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`
  })
}

function PlanCards({ billing, onChange }: { billing: Billing; onChange: (plan: AiPlan) => void }) {
  const { t } = useTranslation()
  return (
    <div>
      <h3 className="m-0 text-body font-semibold">{t('billing.plans.title')}</h3>
      <div className="mt-2 grid gap-3 tablet:grid-cols-2">
        {billing.plans.map((plan) => {
          const current = plan.key === billing.plan
          return (
            <article
              key={plan.key}
              aria-label={t(`billing.plans.${plan.key}`)}
              className={cn(
                'flex flex-col gap-2 rounded-panel border p-4',
                current ? 'border-neel bg-neel-soft' : 'border-line',
              )}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h4 className="m-0 font-display text-h4">{t(`billing.plans.${plan.key}`)}</h4>
                {current ? (
                  <span className="rounded-pill bg-neel px-3 py-0.5 text-caption font-semibold text-on-neel">
                    {t('billing.plans.current')}
                  </span>
                ) : null}
              </div>
              <p className="m-0 text-small text-ink-muted">{plan.summary}</p>
              <p className="m-0 text-small tabular-nums">
                {plan.monthly_questions === null
                  ? t('billing.plans.noLimit')
                  : t('billing.plans.managedLimit', {
                      amount: formatNumber(plan.monthly_questions),
                    })}
              </p>
              <p className="m-0 text-small text-ink-muted">
                {plan.price_inr_per_month === null
                  ? t('billing.plans.priceSoon')
                  : `₹${formatNumber(plan.price_inr_per_month)}`}
              </p>
              {!current ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className="mt-auto w-fit"
                  onClick={() => onChange(plan.key)}
                >
                  {t('billing.plans.change')}
                  <span className="sr-only">: {t(`billing.plans.${plan.key}`)}</span>
                </Button>
              ) : null}
            </article>
          )
        })}
      </div>
    </div>
  )
}

/** Questions used of the Managed plan's monthly limit, with the 80% and 100% states in words (BUILD_SPEC M18). */
function UsageMeter({ limit }: { limit: Limit }) {
  const { t } = useTranslation()
  if (limit.limit === null) {
    return <p className="m-0 mt-2 text-small text-ink-muted">{t('billing.usage.noLimit')}</p>
  }
  const share = limit.limit > 0 ? Math.min(limit.used / limit.limit, 1) : 1
  const text = t('billing.usage.used', {
    used: formatNumber(limit.used),
    limit: formatNumber(limit.limit),
  })
  const date = formatDate(limit.resets_on, 'UTC')
  return (
    <div className="mt-2">
      <p className="m-0 font-display text-h4 leading-snug font-semibold tabular-nums">{text}</p>
      <div
        role="meter"
        aria-label={t('billing.usage.meter')}
        aria-valuemin={0}
        aria-valuemax={limit.limit}
        aria-valuenow={Math.min(limit.used, limit.limit)}
        aria-valuetext={text}
        className="mt-3 h-2 w-full overflow-hidden rounded-pill bg-neel-soft"
      >
        <div
          className={cn(
            'h-full rounded-pill',
            limit.level === 'reached'
              ? 'bg-loss'
              : limit.level === 'warning'
                ? 'bg-caution'
                : 'bg-neel',
          )}
          style={{ width: `${share * 100}%` }}
        />
      </div>
      <p
        className={cn(
          'm-0 mt-2 text-small',
          limit.level === 'reached' ? 'text-loss' : 'text-ink-muted',
        )}
        role={limit.level === 'ok' ? undefined : 'status'}
      >
        {limit.level === 'reached'
          ? t('billing.usage.reached', { date })
          : limit.level === 'warning'
            ? t('billing.usage.warning', { percent: Math.floor(limit.percent ?? 80), date })
            : t('billing.usage.ok', { left: formatNumber(limit.limit - limit.used), date })}
      </p>
    </div>
  )
}

function quantityText(line: SummaryLine, noLimit: string): string {
  if (line.quantity === null) return noLimit
  return line.unit === 'USD' ? formatUsd(line.quantity) : formatNumber(line.quantity)
}

function Summary({ current }: { current: string }) {
  const { t } = useTranslation()
  const [month, setMonth] = useState(current)
  const summary = useQuery({
    queryKey: ['billing-summary', month],
    queryFn: () => billingApi.summary(month),
  })
  const download = useMutation({
    mutationFn: () => billingApi.summaryCsv(month),
    onSuccess: saveFile,
  })
  const data = summary.data
  return (
    <div>
      <h3 className="m-0 text-body font-semibold">{t('billing.summary.title')}</h3>
      <p className="m-0 mt-1 text-small text-ink-muted">{t('billing.summary.intro')}</p>
      <SelectField
        label={t('billing.summary.month')}
        value={month}
        onChange={(event) => setMonth(event.target.value)}
        className="mt-3 max-w-xs"
      >
        {recentMonths(current).map((m) => (
          <option key={m} value={m}>
            {formatMonth(m)}
          </option>
        ))}
      </SelectField>
      {summary.isPending ? (
        <p className="m-0 mt-3 text-small text-ink-muted">{t('billing.summary.loading')}</p>
      ) : null}
      {summary.isError ? (
        <FormAlert tone="error" className="mt-3">
          {t('billing.summary.loadFailed')}
        </FormAlert>
      ) : null}
      {download.isError ? (
        <FormAlert tone="error" className="mt-3">
          {download.error.message}
        </FormAlert>
      ) : null}
      {data ? (
        <>
          <p className="m-0 mt-3 text-small text-ink-muted">
            {data.frozen_at
              ? t('billing.summary.frozen', { date: formatDate(data.frozen_at) })
              : t('billing.summary.draft')}{' '}
            {data.plan.name}
          </p>
          <table className="mt-2 w-full border-collapse text-small">
            <caption className="sr-only">{t('billing.summary.title')}</caption>
            <thead>
              <tr className="border-b border-line text-caption text-ink-muted">
                <th scope="col" className="py-2 pr-3 text-left font-medium">
                  {t('billing.summary.columns.line')}
                </th>
                <th scope="col" className="py-2 text-right font-medium">
                  {t('billing.summary.columns.quantity')}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.code} className="border-b border-line">
                  <th scope="row" className="min-h-11 py-2 pr-3 text-left font-normal break-words">
                    {line.description}
                  </th>
                  <td className="py-2 text-right whitespace-nowrap tabular-nums">
                    {quantityText(line, t('billing.summary.unlimited'))}
                  </td>
                </tr>
              ))}
              <tr>
                <th scope="row" className="py-2 pr-3 text-left font-semibold">
                  {t('billing.summary.amountDue')}
                </th>
                <td className="py-2 text-right tabular-nums">{t('billing.summary.amountNone')}</td>
              </tr>
            </tbody>
          </table>
          {data.plan_changes.length > 0 ? (
            <div className="mt-3">
              <h4 className="m-0 text-small font-semibold">{t('billing.summary.changes')}</h4>
              <ul className="m-0 mt-1 list-disc pl-5 text-small">
                {data.plan_changes.map((change) => (
                  <li key={change.at}>
                    {t('billing.summary.changeRow', {
                      from: change.from ? t(`billing.plans.${change.from}`) : '',
                      to: change.to ? t(`billing.plans.${change.to}`) : '',
                      date: formatDate(change.at),
                    })}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <Button
            variant="secondary"
            className="mt-3"
            disabled={download.isPending}
            onClick={() => download.mutate()}
          >
            {download.isPending ? t('billing.summary.downloading') : t('billing.summary.download')}
          </Button>
        </>
      ) : null}
    </div>
  )
}

function Events({ events }: { events: BillingEvent[] }) {
  const { t } = useTranslation()
  return (
    <div>
      <h3 className="m-0 text-body font-semibold">{t('billing.events.title')}</h3>
      {events.length === 0 ? (
        <p className="m-0 mt-1 text-small text-ink-muted">{t('billing.events.none')}</p>
      ) : (
        <ul className="m-0 mt-1 list-none p-0">
          {events.map((event) => (
            <li
              key={event.id}
              className="flex flex-wrap justify-between gap-2 border-b border-line py-2 text-small"
            >
              <span>
                {event.kind === 'plan_changed'
                  ? `${t('billing.events.plan_changed')}: ${t(`billing.plans.${String(event.details.from ?? '')}`)} → ${t(`billing.plans.${String(event.details.to ?? '')}`)}`
                  : event.period
                    ? `${t(`billing.events.${event.kind}`)} (${formatMonth(event.period)})`
                    : t(`billing.events.${event.kind}`)}
              </span>
              <span className="text-ink-muted">{formatDate(event.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="m-0 mt-2 text-caption text-ink-muted">{t('billing.events.hint')}</p>
    </div>
  )
}

/** Settings > Plan and billing (Owners). Hooks and placeholders only: no payment, no price (D117-D121). */
export default function BillingSection() {
  const { t } = useTranslation()
  const query = useQuery({ queryKey: ['billing'], queryFn: billingApi.get, retry: 1 })
  const [target, setTarget] = useState<AiPlan | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const billing = query.data
  return (
    <section
      aria-labelledby="billing-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6 desktop:col-span-2"
    >
      <h2 id="billing-title" className="text-h4">
        {t('billing.title')}
      </h2>
      <p className="m-0 mt-2 text-small text-ink-muted">{t('billing.intro')}</p>
      <p className="m-0 mt-2 text-small text-ink-muted">{t('billing.notSwitchedOn')}</p>
      {query.isPending ? (
        <p className="m-0 mt-4 text-small text-ink-muted">{t('billing.loading')}</p>
      ) : null}
      {query.isError ? (
        <FormAlert tone="error" className="mt-4">
          {t('billing.loadFailed')}
        </FormAlert>
      ) : null}
      {notice ? (
        <FormAlert tone="success" className="mt-4">
          {notice}
        </FormAlert>
      ) : null}
      {billing ? (
        <div className="mt-4 flex flex-col gap-6">
          <div>
            <h3 className="m-0 text-body font-semibold">{t('billing.usage.title')}</h3>
            <UsageMeter limit={billing.limit} />
          </div>
          <PlanCards
            billing={billing}
            onChange={(plan) => {
              setNotice(null)
              setTarget(plan)
            }}
          />
          <Summary current={billing.limit.month} />
          <Events events={billing.events} />
        </div>
      ) : null}
      {target && billing ? (
        <Suspense fallback={null}>
          <ChangePlanDialog
            target={target}
            plans={billing.plans}
            onClose={() => setTarget(null)}
            onDone={setNotice}
          />
        </Suspense>
      ) : null}
    </section>
  )
}
