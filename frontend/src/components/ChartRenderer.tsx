import { Component, lazy, Suspense, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { ChartSpec } from '../lib/chatApi'

const ChartCanvas = lazy(() => import('./ChartCanvas'))

/** A chart that cannot be drawn never takes the answer down with it: the title and summary stay. */
class ChartBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? null : this.props.children
  }
}

/**
 * A chart from an answer: the title states the insight, the chart itself is loaded on demand (ECharts is large,
 * the shell stays under its JS budget), and screen readers get a text summary (11a.9).
 */
export function ChartRenderer({ spec }: { spec: ChartSpec }) {
  const { t } = useTranslation()
  const summary = t('ask.answer.chartSummary', {
    title: spec.title,
    series: spec.series.map((s) => s.name).join(', '),
    points: spec.x.values.length,
  })
  return (
    <figure className="m-0">
      <figcaption className="mb-2 text-small font-semibold text-ink">{spec.title}</figcaption>
      <div role="img" aria-label={summary} className="min-h-[260px]">
        <ChartBoundary>
          <Suspense fallback={<div className="h-[260px] rounded-sheet bg-paper" />}>
            <ChartCanvas spec={spec} />
          </Suspense>
        </ChartBoundary>
      </div>
    </figure>
  )
}
