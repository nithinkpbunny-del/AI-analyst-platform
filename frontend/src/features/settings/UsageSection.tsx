import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { SelectField } from '../../components/ui/select'
import { aiApi, formatMonth, formatTokens, formatUsd, type Usage } from '../../lib/aiApi'
import { cn } from '../../lib/cn'
import { formatDate, formatNumber } from '../../lib/format'
import { useCan } from '../../lib/permissions'

const CAUTION_AT = 0.8

/** Questions used of the Managed plan's monthly limit, as a bar with a text equivalent. */
function LimitMeter({ usage }: { usage: Usage & { limit: number } }) {
  const { t } = useTranslation()
  const used = usage.totals.limited_questions
  const share = usage.limit > 0 ? Math.min(used / usage.limit, 1) : 1
  const reached = used >= usage.limit
  const resets = formatDate(usage.resets_on, 'UTC')
  const text = t('usage.questionsOfLimit', {
    used: formatNumber(used),
    limit: formatNumber(usage.limit),
  })
  return (
    <div className="mt-4">
      <p className="m-0 font-display text-h4 leading-snug font-semibold text-balance tabular-nums tablet:text-h3">
        {text}
      </p>
      <div
        role="meter"
        aria-label={t('usage.meterLabel')}
        aria-valuemin={0}
        aria-valuemax={usage.limit}
        aria-valuenow={Math.min(used, usage.limit)}
        aria-valuetext={text}
        className="mt-3 h-2 w-full overflow-hidden rounded-pill bg-neel-soft"
      >
        <div
          className={cn(
            'h-full rounded-pill',
            reached ? 'bg-loss' : share >= CAUTION_AT ? 'bg-caution' : 'bg-neel',
          )}
          style={{ width: `${share * 100}%` }}
        />
      </div>
      {usage.is_current_month ? (
        <p className={cn('m-0 mt-2 text-small', reached ? 'text-loss' : 'text-ink-muted')}>
          {reached
            ? t('usage.limitReached', { date: resets })
            : t('usage.questionsLeft', {
                left: formatNumber(usage.limit - used),
                date: resets,
              })}
        </p>
      ) : null}
    </div>
  )
}

function Totals({ usage }: { usage: Usage }) {
  const { t } = useTranslation()
  const totals = usage.totals
  const rows: { label: string; value: string; note?: string }[] = [
    {
      label: t('usage.questions'),
      value: formatNumber(totals.questions),
      note: t('usage.questionsNote'),
    },
    { label: t('usage.briefings'), value: formatNumber(totals.briefings) },
    {
      label: t('usage.tokens'),
      value: t('usage.tokensValue', {
        input: formatTokens(totals.input_tokens),
        output: formatTokens(totals.output_tokens),
      }),
    },
    {
      label: t('usage.cost'),
      value: formatUsd(totals.cost_usd),
      note:
        totals.cost_unknown_calls > 0
          ? `${t('usage.costNote')} ${t('usage.costUnknown', { count: totals.cost_unknown_calls })}`
          : t('usage.costNote'),
    },
  ]
  return (
    <dl className="m-0 mt-4 grid grid-cols-[auto_1fr] text-small">
      {rows.map((row, index) => (
        <div key={row.label} className="contents">
          <dt
            className={cn(
              'flex min-h-11 items-start pt-3 pr-6 text-ink-muted',
              index < rows.length - 1 && 'border-b border-line',
            )}
          >
            {row.label}
          </dt>
          <dd
            className={cn(
              'm-0 flex min-h-11 flex-col justify-center py-3',
              index < rows.length - 1 && 'border-b border-line',
            )}
          >
            <span className="tabular-nums">{row.value}</span>
            {row.note ? <span className="text-caption text-ink-muted">{row.note}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function People({ usage }: { usage: Usage }) {
  const { t } = useTranslation()
  if (usage.people.length === 0) return null
  return (
    <>
      <h3 className="m-0 mt-6 text-body font-semibold">{t('usage.people')}</h3>
      <table className="mt-2 w-full border-collapse text-small">
        <thead>
          <tr className="border-b border-line text-caption text-ink-muted">
            <th scope="col" className="py-2 pr-3 text-left font-medium">
              {t('usage.columns.person')}
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              {t('usage.columns.questions')}
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              {t('usage.columns.tokens')}
            </th>
            <th scope="col" className="py-2 text-right font-medium">
              {t('usage.columns.cost')}
            </th>
          </tr>
        </thead>
        <tbody>
          {usage.people.map((person) => (
            <tr key={person.user_id ?? 'system'} className="border-b border-line last:border-b-0">
              <th scope="row" className="h-11 py-2 pr-3 text-left font-normal">
                <span className="block break-words">{person.name ?? t('usage.noPerson')}</span>
                {person.email ? (
                  <span className="block break-all text-caption text-ink-muted">
                    {person.email}
                  </span>
                ) : null}
              </th>
              <td className="py-2 pr-3 text-right tabular-nums">
                {formatNumber(person.questions)}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">
                {formatTokens(person.input_tokens + person.output_tokens)}
              </td>
              <td className="py-2 text-right tabular-nums">{formatUsd(person.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function History({ usage }: { usage: Usage }) {
  const { t } = useTranslation()
  const months = [...usage.history].reverse()
  return (
    <>
      <h3 className="m-0 mt-6 text-body font-semibold">{t('usage.history')}</h3>
      <ul className="m-0 mt-2 list-none p-0 text-small">
        {months.map((month) => (
          <li
            key={month.month}
            className="flex min-h-11 items-center justify-between gap-4 border-b border-line last:border-b-0"
          >
            <span>{formatMonth(month.month)}</span>
            <span className="tabular-nums text-ink-muted">
              {t('usage.historyRow', {
                questions: formatNumber(month.questions),
                cost: formatUsd(month.cost_usd),
              })}
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}

/** AI usage for Owners (DECISIONS D42): the monthly limit, totals, per person and the last six months. */
export function UsageSection() {
  const { t } = useTranslation()
  const canSee = useCan('org.manage')
  const [month, setMonth] = useState<string | undefined>()
  const query = useQuery({
    queryKey: ['usage', month ?? 'current'],
    queryFn: () => aiApi.usage(month),
    enabled: canSee,
    placeholderData: (previous) => previous,
  })
  if (!canSee) return null
  const usage = query.data
  const months = usage ? [...usage.history].reverse().map((h) => h.month) : []

  return (
    <section
      aria-labelledby="usage-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="usage-title" className="text-h4">
        {t('usage.title')}
      </h2>
      <p className="m-0 mt-2 text-small text-ink-muted">{t('usage.intro')}</p>
      {query.isPending ? (
        <p role="status" className="m-0 mt-4 text-small text-ink-muted">
          {t('usage.loading')}
        </p>
      ) : null}
      {query.isError ? (
        <FormAlert tone="error" className="mt-4">
          {t('usage.loadFailed')}
        </FormAlert>
      ) : null}
      {usage ? (
        <>
          <SelectField
            className="mt-4"
            label={t('usage.month')}
            name="usage-month"
            value={usage.month}
            onChange={(event) => setMonth(event.target.value)}
          >
            {months.map((value) => (
              <option key={value} value={value}>
                {formatMonth(value)}
              </option>
            ))}
          </SelectField>
          {usage.limit !== null ? (
            <LimitMeter usage={{ ...usage, limit: usage.limit }} />
          ) : (
            <p className="m-0 mt-4 text-small">{t('usage.noLimit')}</p>
          )}
          <Totals usage={usage} />
          {usage.totals.questions + usage.totals.briefings + usage.totals.checks === 0 ? (
            <p className="m-0 mt-4 text-small text-ink-muted">{t('usage.nothingYet')}</p>
          ) : null}
          <People usage={usage} />
          <History usage={usage} />
        </>
      ) : null}
    </section>
  )
}
