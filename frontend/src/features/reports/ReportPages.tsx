import { useQuery } from '@tanstack/react-query'
import { FileDown, Inbox, Share2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { formatDateTime } from '../../lib/format'
import { useCan } from '../../lib/permissions'
import { reportsApi, type ShareTarget } from '../../lib/reportsApi'
import { AnswerView } from '../ask/AnswerView'
import { ExportDialog } from './ExportDialog'
import { ShareDialog } from './ShareDialog'
import { answerMessage } from './adapt'
import { BriefingView } from './views'

function useTitle(title: string | undefined) {
  const { t } = useTranslation()
  useEffect(() => {
    if (title) document.title = `${title} · ${t('app.name')}`
  }, [title, t])
}

function Actions({
  target,
  share,
}: {
  target: ShareTarget
  share?: { kind: 'message' | 'report'; id: string }
}) {
  const { t } = useTranslation()
  const [exporting, setExporting] = useState(false)
  const [sharing, setSharing] = useState(false)
  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="secondary" onClick={() => setExporting(true)}>
        <FileDown aria-hidden />
        {t('ask.actions.export')}
      </Button>
      {share ? (
        <Button variant="secondary" onClick={() => setSharing(true)}>
          <Share2 aria-hidden />
          {t('ask.actions.share')}
        </Button>
      ) : null}
      <ExportDialog target={target} open={exporting} onOpenChange={setExporting} />
      {share ? (
        <ShareDialog
          kind={share.kind}
          objectId={share.id}
          open={sharing}
          onOpenChange={setSharing}
        />
      ) : null}
    </div>
  )
}

/** ``/reports/:reportId``: a long task's report or a briefing of mine. */
export function ReportPage() {
  const { t } = useTranslation()
  const { reportId = '' } = useParams()
  // Viewers export only items shared with them (BUILD_SPEC 6.2); Owners and Analysts export their own.
  const canShare = useCan('share')
  const navigate = useNavigate()
  const report = useQuery({
    queryKey: ['report', reportId],
    queryFn: () => reportsApi.report(reportId),
    retry: false,
  })
  const data = report.data
  useTitle(data?.title)

  if (report.isPending)
    return <p className="m-0 text-small text-ink-muted">{t('reports.loading')}</p>
  if (!data) {
    return (
      <EmptyState
        className="max-w-2xl"
        message={t('reports.notFound')}
        action={
          <Button asChild variant="secondary">
            <Link to="/today">{t('reports.back')}</Link>
          </Button>
        }
      />
    )
  }
  const body = data.body
  const subtitle =
    body.kind === 'briefing' ? t('reports.briefingSubtitle') : t('reports.taskSubtitle')
  return (
    <div className="max-w-conversation">
      <PageHeader
        title={data.title}
        intro={`${subtitle} · ${t('reports.madeOn', { when: formatDateTime(data.created_at) })}`}
      >
        {canShare ? (
          <Actions
            target={{ kind: 'report', reportId: data.id }}
            share={{ kind: 'report', id: data.id }}
          />
        ) : null}
      </PageHeader>
      {body.kind === 'briefing' ? (
        <BriefingView
          body={body}
          provenance={data.provenance}
          quality={data.quality_warnings}
          createdAt={data.created_at}
        />
      ) : (
        <div className="flex flex-col gap-4">
          <p className="m-0 rounded-control bg-neel-soft p-3 text-body">
            <span className="sr-only">{t('reports.question')}: </span>
            {body.question}
          </p>
          <AnswerView
            message={answerMessage({
              id: data.id,
              answer: body.answer,
              provenance: data.provenance,
              quality: data.quality_warnings,
              createdAt: data.created_at,
            })}
            onNextQuestion={(q) => void navigate(`/ask?q=${encodeURIComponent(q)}`)}
          />
        </div>
      )}
    </div>
  )
}

/** ``/shared``: answers and reports others shared with me. */
export function SharedPage() {
  const { t } = useTranslation()
  const items = useQuery({ queryKey: ['shared'], queryFn: reportsApi.shared })
  useTitle(t('reports.sharedTitle'))
  return (
    <div className="max-w-conversation">
      <PageHeader title={t('reports.sharedTitle')} intro={t('reports.sharedIntro')} />
      {items.data && items.data.items.length === 0 ? (
        <EmptyState icon={Inbox} message={t('reports.sharedEmpty')} />
      ) : (
        <ul className="m-0 list-none p-0">
          {(items.data?.items ?? []).map((item) => (
            <li key={item.id} className="border-b border-line">
              <Link
                to={`/shared/${item.id}`}
                className="flex min-h-11 flex-col gap-1 py-3 text-ink no-underline hover:text-neel"
              >
                <span className="font-semibold">{item.title}</span>
                <span className="text-small text-ink-muted">
                  {item.shared_by ? t('today.shared.by', { name: item.shared_by }) : null}
                  {' · '}
                  {formatDateTime(item.created_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** ``/shared/:shareId``: one item shared with me (Viewers may export it). */
export function SharedItemPage() {
  const { t } = useTranslation()
  const { shareId = '' } = useParams()
  const canSee = useCan('shared.view')
  const item = useQuery({
    queryKey: ['shared', shareId],
    queryFn: () => reportsApi.sharedItem(shareId),
    retry: false,
  })
  const data = item.data
  useTitle(data?.title)
  if (item.isPending) return <p className="m-0 text-small text-ink-muted">{t('reports.loading')}</p>
  if (!data) {
    return (
      <EmptyState
        className="max-w-2xl"
        message={t('reports.notFound')}
        action={
          <Button asChild variant="secondary">
            <Link to="/shared">{t('reports.sharedTitle')}</Link>
          </Button>
        }
      />
    )
  }
  return (
    <div className="max-w-conversation">
      <PageHeader
        title={data.title}
        intro={data.shared_by ? t('reports.sharedBy', { name: data.shared_by }) : undefined}
      >
        {canSee ? <Actions target={{ kind: 'shared', shareId: data.id }} /> : null}
      </PageHeader>
      {data.body ? (
        <BriefingView
          body={data.body}
          provenance={data.provenance}
          quality={data.quality_warnings}
          createdAt={data.created_at}
        />
      ) : data.answer ? (
        <div className="flex flex-col gap-4">
          {data.question ? (
            <p className="m-0 rounded-control bg-neel-soft p-3 text-body">{data.question}</p>
          ) : null}
          <AnswerView
            message={answerMessage({
              id: data.id,
              answer: data.answer,
              provenance: data.provenance,
              quality: data.quality_warnings,
              createdAt: data.created_at,
            })}
            onNextQuestion={() => undefined}
          />
        </div>
      ) : (
        <FormAlert tone="error">{t('reports.notFound')}</FormAlert>
      )}
    </div>
  )
}
