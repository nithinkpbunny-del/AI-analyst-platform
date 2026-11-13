import { useTranslation } from 'react-i18next'
import { KpiTile } from '../../components/KpiTile'
import { QualityBanner } from '../../components/QualityBanner'
import type { QualityWarning, ResultProvenance } from '../../lib/chatApi'
import { formatDate } from '../../lib/format'
import type { BriefingBody, KpiFact } from '../../lib/reportsApi'
import { qualityText } from '../connections/text'
import { RichText } from '../ask/RichText'
import { CITATION, useSources } from '../ask/sources'
import { noteText } from '../ask/text'
import { answerMessage } from './adapt'

function citedIn(texts: string[]): string[] {
  const out: string[] = []
  for (const text of texts) {
    for (const match of text.matchAll(CITATION)) {
      for (const rid of (match[1] ?? '').split(',').map((r) => r.trim()))
        if (rid && !out.includes(rid)) out.push(rid)
    }
  }
  return out
}

/** Sources (numbered marks with popovers) for a briefing, like an answer's. */
function useBriefingSources(
  body: BriefingBody,
  provenance: Record<string, ResultProvenance>,
  createdAt: string,
) {
  const texts = [body.headline, ...body.sections.flatMap((s) => s.items)]
  const pseudo = answerMessage({
    id: 'briefing',
    answer: {
      lead: body.headline,
      key_figure: body.key_figure,
      evidence: [],
      why_it_matters: null,
      recommendation: null,
      data_notes: [],
      notes: [],
      next_questions: [],
      charts: [],
      confidence: null,
      cited: citedIn(texts),
    },
    provenance,
    quality: [],
    createdAt,
  })
  return useSources(pseudo)
}

function metricLabel(metric: string): string {
  return { revenue: 'Revenue', orders: 'Orders', aov: 'Average order value' }[metric] ?? metric
}

export function KpiRow({ kpis, span = 'week' }: { kpis: KpiFact[]; span?: 'day' | 'week' }) {
  const { t } = useTranslation()
  const shown = kpis.filter((k) => k.span === span && k.current !== null).slice(0, 3)
  if (shown.length === 0) return null
  return (
    <section aria-label={t('today.briefing.kpisLabel')} className="grid gap-3 tablet:grid-cols-3">
      {shown.map((kpi) => (
        <KpiTile
          key={`${kpi.span}-${kpi.metric}`}
          label={t(span === 'week' ? 'today.kpi.week' : 'today.kpi.day', {
            metric: metricLabel(kpi.metric),
          })}
          value={kpi.current ?? 0}
          format={kpi.unit === 'INR' ? 'inr' : 'number'}
          deltaPct={kpi.change_pct ?? undefined}
          comparison={t(span === 'week' ? 'today.kpi.vsWeek' : 'today.kpi.vsDay')}
          goodDirection={kpi.good_direction ?? 'up'}
        />
      ))}
    </section>
  )
}

/** A briefing: headline with the haldi mark, key numbers, the sections, data notes (BUILD_SPEC 13.3). */
export function BriefingView({
  body,
  provenance,
  quality,
  createdAt,
  showKpis = true,
  limit,
}: {
  body: BriefingBody
  provenance: Record<string, ResultProvenance>
  quality: QualityWarning[]
  createdAt: string
  showKpis?: boolean
  /** Show at most this many items per section (Today shows the top items). */
  limit?: number
}) {
  const { t } = useTranslation()
  const sources = useBriefingSources(body, provenance, createdAt)
  const notes = (body.notes ?? []).map((note) => noteText(t, note))
  return (
    <div className="flex flex-col gap-4">
      {quality.slice(0, 2).map((warning, i) => (
        <QualityBanner key={`${warning.table}-${i}`}>
          {warning.name ?? warning.table}: {qualityText(t, warning)}
        </QualityBanner>
      ))}
      <article className="rounded-sheet border border-line bg-surface p-4 tablet:p-6">
        {body.data_as_of ? (
          <p className="m-0 text-caption text-ink-muted">
            {t('today.briefing.dataAsOf', { date: formatDate(body.data_as_of) })}
          </p>
        ) : null}
        <p className="m-0 mt-1 font-display text-h4 leading-snug tablet:text-h3">
          <RichText text={body.headline} sources={sources} keyFigure={body.key_figure} />
        </p>
        {body.sections.map((section) => (
          <section key={section.key} className="mt-5">
            <h3 className="m-0 text-small font-semibold">{t(`reports.sections.${section.key}`)}</h3>
            <ul className="m-0 mt-1 list-none p-0">
              {section.items.slice(0, limit).map((item, i) => (
                <li
                  key={i}
                  className="flex min-h-11 items-center border-b border-line py-2 text-body"
                >
                  <span>
                    <RichText text={item} sources={sources} />
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {notes.length > 0 ? (
          <section className="mt-5">
            <h3 className="m-0 text-small font-semibold">{t('ask.answer.dataNotes')}</h3>
            {notes.map((note, i) => (
              <p key={i} className="m-0 mt-1 text-small text-caution">
                {note}
              </p>
            ))}
          </section>
        ) : null}
        {body.written_by === 'code' ? (
          <p className="m-0 mt-4 text-caption text-ink-muted">{t('reports.writtenByCode')}</p>
        ) : null}
      </article>
      {showKpis ? <KpiRow kpis={body.kpis ?? []} /> : null}
    </div>
  )
}
