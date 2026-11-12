import { TriangleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { AnswerSheet } from '../../components/AnswerSheet'
import { ChartRenderer } from '../../components/ChartRenderer'
import { ProgressSteps } from '../../components/ProgressSteps'
import { QualityBanner } from '../../components/QualityBanner'
import { Button } from '../../components/ui/button'
import { isWorking, type ChatMessage } from '../../lib/chatApi'
import { formatINR } from '../../lib/format'
import { qualityText } from '../connections/text'
import { progressSteps } from './progress'
import { RichText } from './RichText'
import { useSources } from './sources'
import { errorAction, errorText, noteText } from './text'

export function AnswerView({
  message,
  onNextQuestion,
  onSave,
  onShowWork,
  onRetry,
}: {
  message: ChatMessage
  onNextQuestion: (question: string) => void
  onSave?: () => void
  onShowWork: () => void
  onRetry?: () => void
}) {
  const { t } = useTranslation()
  const sources = useSources(message)

  if (isWorking(message)) {
    return (
      <section
        aria-label={t('ask.progress.title')}
        className="max-w-conversation rounded-sheet border border-line bg-surface p-4 tablet:p-6"
      >
        <h3 className="m-0 mb-3 text-small font-semibold">{t('ask.progress.title')}</h3>
        <ProgressSteps steps={progressSteps(t, message)} />
      </section>
    )
  }

  if (message.status === 'failed' || !message.answer) {
    const action = errorAction(message.error_code)
    return (
      <section
        role="alert"
        className="max-w-conversation rounded-sheet border border-line bg-surface p-4 tablet:p-6"
      >
        <p className="m-0 flex items-start gap-2 text-body">
          <TriangleAlert className="mt-1 size-4 shrink-0 text-caution" aria-hidden />
          <span>
            <span className="font-semibold">{t('ask.errors.title')}. </span>
            {errorText(t, message.error_code)}
          </span>
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {action === 'connect' ? (
            <Button asChild variant="secondary" size="sm">
              <Link to="/connections">{t('ask.errors.connect')}</Link>
            </Button>
          ) : action === 'settings' ? (
            <Button asChild variant="secondary" size="sm">
              <Link to="/settings">{t('ask.errors.settings')}</Link>
            </Button>
          ) : onRetry ? (
            <Button variant="secondary" size="sm" onClick={onRetry}>
              {t('ask.errors.askAgain')}
            </Button>
          ) : null}
          {message.tool_trace.length > 0 ? (
            <Button variant="ghost" size="sm" onClick={onShowWork}>
              {t('ask.work.open')}
            </Button>
          ) : null}
        </div>
      </section>
    )
  }

  const answer = message.answer
  const notes = [
    ...answer.data_notes,
    ...answer.notes.map((note) => noteText(t, note)),
    ...(answer.confidence === 'low' ? [t('ask.answer.confidence_low')] : []),
  ]
  const warnings = message.quality_warnings.slice(0, 3)
  const recommendation = answer.recommendation
  return (
    <div className="flex max-w-conversation flex-col gap-3">
      {warnings.map((warning, i) => (
        <QualityBanner key={`${warning.table}-${warning.kind}-${i}`}>
          {warning.name ?? warning.table}: {qualityText(t, warning)}
        </QualityBanner>
      ))}
      <AnswerSheet
        lead={<RichText text={answer.lead} sources={sources} keyFigure={answer.key_figure} />}
        evidence={answer.evidence.map((item, i) => ({
          id: `e${i}`,
          content: (
            <span>
              <RichText text={item} sources={sources} />
            </span>
          ),
        }))}
        chart={
          answer.charts.length > 0 ? (
            <div className="flex flex-col gap-4">
              {answer.charts.map((chart) => (
                <ChartRenderer key={chart.chart_id} spec={chart} />
              ))}
            </div>
          ) : undefined
        }
        whyItMatters={
          answer.why_it_matters ? (
            <RichText text={answer.why_it_matters} sources={sources} />
          ) : undefined
        }
        recommendation={
          recommendation ? (
            <>
              <RichText text={recommendation.text} sources={sources} />
              {recommendation.impact_inr !== null ? (
                <p className="m-0 mt-1 text-small text-ink-muted tabular">
                  {t('ask.answer.impact', { amount: formatINR(recommendation.impact_inr) })}
                </p>
              ) : null}
            </>
          ) : undefined
        }
        dataNotes={notes}
        nextQuestions={answer.next_questions}
        onNextQuestion={onNextQuestion}
        onSave={onSave}
        onShowWork={onShowWork}
      />
      {message.reused ? (
        <p className="m-0 text-caption text-ink-muted">{t('ask.answer.reused')}</p>
      ) : null}
    </div>
  )
}
