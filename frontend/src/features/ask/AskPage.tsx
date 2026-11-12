import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bookmark, History, MessageSquareText, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { Sheet, SheetContent } from '../../components/ui/sheet'
import { ApiError } from '../../lib/api'
import {
  chatApi,
  isWorking,
  LENSES,
  type ChatMessage,
  type Conversation,
  type ConversationSummary,
  type Lens,
  type SavedQuestion,
} from '../../lib/chatApi'
import { cn } from '../../lib/cn'
import { formatRelativeTime } from '../../lib/format'
import { useCan } from '../../lib/permissions'
import { AnswerView } from './AnswerView'
import { AskBox } from './AskBox'
import { ShowMyWork } from './ShowMyWork'

const CONVERSATIONS = ['conversations'] as const
const SAVED = ['saved-questions'] as const
const POLL_MS = 1000

function conversationKey(id: string) {
  return ['conversation', id] as const
}

function lastMessage(conversation: Conversation | undefined): ChatMessage | undefined {
  return conversation?.messages[conversation.messages.length - 1]
}

/** `/` focuses the question box from anywhere on the page (BUILD_SPEC 11a.8). */
function useSlashFocus(target: React.RefObject<HTMLTextAreaElement | null>) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return
      const active = document.activeElement
      if (
        active instanceof HTMLInputElement ||
        active instanceof HTMLTextAreaElement ||
        (active instanceof HTMLElement && active.isContentEditable)
      ) {
        return
      }
      event.preventDefault()
      target.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [target])
}

function LensPicker({ value, onChange }: { value: Lens; onChange: (lens: Lens) => void }) {
  const { t } = useTranslation()
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className="mb-1 text-small font-medium">{t('ask.lens.label')}</legend>
      <div className="flex flex-wrap gap-1 rounded-control border border-line bg-surface p-1">
        {LENSES.map((lens) => (
          <label
            key={lens}
            title={t(`ask.lens.${lens}Hint`)}
            className={cn(
              'flex min-h-11 cursor-pointer items-center rounded-control px-3 text-small has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-neel',
              value === lens
                ? 'bg-neel-soft font-medium text-ink'
                : 'text-ink-muted hover:text-ink',
            )}
          >
            <input
              type="radio"
              name="lens"
              value={lens}
              checked={value === lens}
              onChange={() => onChange(lens)}
              className="sr-only"
            />
            {t(`ask.lens.${lens}`)}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function HistoryList({
  conversations,
  activeId,
  onPick,
}: {
  conversations: ConversationSummary[]
  activeId?: string
  onPick?: () => void
}) {
  const { t } = useTranslation()
  if (conversations.length === 0) {
    return <p className="m-0 text-small text-ink-muted">{t('ask.historyEmpty')}</p>
  }
  return (
    <ul className="m-0 list-none p-0">
      {conversations.map((c) => (
        <li key={c.id}>
          <Link
            to={`/ask/${c.id}`}
            onClick={onPick}
            aria-current={c.id === activeId ? 'page' : undefined}
            className={cn(
              'block min-h-11 rounded-control px-3 py-2 text-small hover:bg-neel-soft',
              c.id === activeId && 'bg-neel-soft font-medium',
            )}
          >
            <span className="line-clamp-2">{c.title}</span>
            <span className="block text-caption text-ink-muted">
              {formatRelativeTime(c.last_message_at)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}

function SavedList({
  saved,
  onRun,
  onRemove,
}: {
  saved: SavedQuestion[]
  onRun: (q: SavedQuestion) => void
  onRemove: (q: SavedQuestion) => void
}) {
  const { t } = useTranslation()
  if (saved.length === 0) {
    return <p className="m-0 text-small text-ink-muted">{t('ask.saved.empty')}</p>
  }
  return (
    <ul className="m-0 list-none p-0">
      {saved.map((q) => (
        <li key={q.id} className="flex items-start gap-1">
          <button
            type="button"
            onClick={() => onRun(q)}
            className="min-h-11 flex-1 rounded-control px-3 py-2 text-left text-small hover:bg-neel-soft"
          >
            {q.text}
            <span className="block text-caption text-ink-muted">{t(`ask.lens.${q.lens}`)}</span>
          </button>
          <button
            type="button"
            onClick={() => onRemove(q)}
            aria-label={t('ask.saved.remove', { text: q.text })}
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-control text-ink-muted hover:bg-neel-soft"
          >
            <X className="size-4" aria-hidden />
          </button>
        </li>
      ))}
    </ul>
  )
}

function askError(t: ReturnType<typeof useTranslation>['t'], error: unknown): string {
  return error instanceof ApiError ? error.message : t('ask.errors.internal')
}

export function AskPage() {
  const { t } = useTranslation()
  const { conversationId } = useParams()
  // `/ask?q=...` opens the box with a question already typed (the What-if screen uses it).
  const [searchParams] = useSearchParams()
  const typed = conversationId ? undefined : (searchParams.get('q') ?? undefined)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const canAsk = useCan('ask')
  const canSave = useCan('saved_questions')
  const box = useRef<HTMLTextAreaElement>(null)
  useSlashFocus(box)
  const [lens, setLens] = useState<Lens>('business')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [work, setWork] = useState<ChatMessage | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    document.title = `${t('pages.ask.title')} · ${t('app.name')}`
  }, [t])

  const conversations = useQuery({
    queryKey: CONVERSATIONS,
    queryFn: chatApi.list,
    enabled: canAsk,
  })
  const saved = useQuery({ queryKey: SAVED, queryFn: chatApi.saved, enabled: canSave })
  const conversation = useQuery({
    queryKey: conversationKey(conversationId ?? ''),
    queryFn: () => chatApi.get(conversationId ?? ''),
    enabled: canAsk && Boolean(conversationId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    refetchInterval: (query) => (isWorking(lastMessage(query.state.data)) ? POLL_MS : false),
  })
  const current = conversation.data
  const working = isWorking(lastMessage(current))
  const activeLens = current?.lens ?? lens

  useEffect(() => {
    // When an answer finishes, the history order changes.
    if (current && !working) void queryClient.invalidateQueries({ queryKey: CONVERSATIONS })
  }, [current, working, queryClient])

  const start = useMutation({
    mutationFn: ({ question, lens: chosen }: { question: string; lens: Lens }) =>
      chatApi.start(question, chosen),
    onSuccess: (created) => {
      queryClient.setQueryData(conversationKey(created.id), created)
      void queryClient.invalidateQueries({ queryKey: CONVERSATIONS })
      navigate(`/ask/${created.id}`)
    },
  })
  const followUp = useMutation({
    mutationFn: (question: string) => chatApi.followUp(conversationId ?? '', question),
    onSuccess: (updated) => queryClient.setQueryData(conversationKey(updated.id), updated),
  })
  const changeLens = useMutation({
    mutationFn: (next: Lens) => chatApi.update(conversationId ?? '', { lens: next }),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: conversationKey(conversationId ?? '') }),
  })
  const remove = useMutation({
    mutationFn: () => chatApi.remove(conversationId ?? ''),
    onSuccess: () => {
      setDeleting(false)
      setNotice(t('ask.deleted'))
      void queryClient.invalidateQueries({ queryKey: CONVERSATIONS })
      navigate('/ask')
    },
  })
  const save = useMutation({
    mutationFn: ({ text, lens: chosen }: { text: string; lens: Lens }) =>
      chatApi.save(text, chosen),
    onSuccess: () => {
      setNotice(t('ask.saved.saveDone'))
      void queryClient.invalidateQueries({ queryKey: SAVED })
    },
  })
  const unsave = useMutation({
    mutationFn: (q: SavedQuestion) => chatApi.unsave(q.id),
    onSuccess: () => {
      setNotice(t('ask.saved.removed'))
      void queryClient.invalidateQueries({ queryKey: SAVED })
    },
  })

  function ask(question: string, chosen: Lens = activeLens) {
    setNotice(null)
    if (conversationId && current) followUp.mutate(question)
    else start.mutate({ question, lens: chosen })
  }

  function askFresh(question: string, chosen: Lens) {
    setNotice(null)
    setHistoryOpen(false)
    start.mutate({ question, lens: chosen })
  }

  if (!canAsk) {
    return (
      <>
        <PageHeader title={t('pages.ask.title')} intro={t('pages.ask.intro')} />
        <EmptyState className="max-w-2xl" icon={MessageSquareText} message={t('ask.viewerNote')} />
      </>
    )
  }

  const isSaved = (text: string, chosen: Lens) =>
    (saved.data ?? []).some((q) => q.text === text && q.lens === chosen)
  const busy = start.isPending || followUp.isPending
  const failure = start.error ?? followUp.error
  const sidebar = (onPick?: () => void) => (
    <div className="flex flex-col gap-6">
      <Button asChild variant="secondary" className="justify-start">
        <Link to="/ask" onClick={onPick}>
          <Plus aria-hidden strokeWidth={1.5} />
          {t('ask.newQuestion')}
        </Link>
      </Button>
      <section aria-labelledby="ask-history">
        <h2 id="ask-history" className="m-0 mb-2 font-sans text-small font-semibold">
          {t('ask.history')}
        </h2>
        <HistoryList
          conversations={conversations.data ?? []}
          activeId={conversationId}
          onPick={onPick}
        />
      </section>
      {canSave ? (
        <section aria-labelledby="ask-saved">
          <h2 id="ask-saved" className="m-0 mb-2 font-sans text-small font-semibold">
            {t('ask.saved.title')}
          </h2>
          <SavedList
            saved={saved.data ?? []}
            onRun={(q) => askFresh(q.text, q.lens)}
            onRemove={(q) => unsave.mutate(q)}
          />
        </section>
      ) : null}
    </div>
  )

  return (
    <div className="desktop:grid desktop:grid-cols-[240px_minmax(0,1fr)] desktop:gap-8">
      <aside className="hidden desktop:block" aria-label={t('ask.history')}>
        {sidebar()}
      </aside>
      <div className="min-w-0">
        <PageHeader
          title={current?.title ?? t('pages.ask.title')}
          intro={current ? undefined : t('pages.ask.intro')}
        >
          <div className="flex gap-2">
            <Button
              variant="secondary"
              className="desktop:hidden"
              onClick={() => setHistoryOpen(true)}
            >
              <History aria-hidden strokeWidth={1.5} />
              {t('ask.historyOpen')}
            </Button>
            {current ? (
              <Button variant="ghost" onClick={() => setDeleting(true)}>
                <Trash2 aria-hidden strokeWidth={1.5} />
                {t('ask.deleteConversation')}
              </Button>
            ) : null}
          </div>
        </PageHeader>

        {notice ? (
          <FormAlert tone="success" className="mb-4 max-w-conversation">
            {notice}
          </FormAlert>
        ) : null}

        {conversationId && conversation.isError ? (
          <EmptyState
            icon={MessageSquareText}
            message={t('ask.notFound')}
            action={
              <Button asChild>
                <Link to="/ask">{t('ask.newQuestion')}</Link>
              </Button>
            }
          />
        ) : null}

        {!conversationId ? (
          <div className="flex max-w-conversation flex-col gap-6">
            <LensPicker value={lens} onChange={setLens} />
            <section aria-labelledby="ask-starters">
              <h2 id="ask-starters" className="m-0 mb-2 font-sans text-small font-semibold">
                {t('ask.starters.title')}
              </h2>
              <div className="flex flex-wrap gap-2">
                {(['q1', 'q2', 'q3'] as const).map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => askFresh(t(`ask.starters.${key}`), lens)}
                    className="min-h-11 rounded-pill border border-line-strong px-4 text-left text-small text-ink hover:bg-neel-soft"
                  >
                    {t(`ask.starters.${key}`)}
                  </button>
                ))}
              </div>
            </section>
          </div>
        ) : null}

        {current ? (
          <ol aria-label={t('ask.history')} className="m-0 flex list-none flex-col gap-4 p-0">
            {current.messages.map((message, i) =>
              message.role === 'user' ? (
                <li key={message.id} className="max-w-conversation">
                  <p className="sr-only">{t('ask.you')}</p>
                  <div className="flex items-start justify-between gap-2 rounded-sheet bg-neel-soft px-4 py-3 text-body">
                    <span className="min-w-0 break-words">{message.content}</span>
                    {canSave ? (
                      <button
                        type="button"
                        onClick={() => save.mutate({ text: message.content, lens: message.lens })}
                        disabled={isSaved(message.content, message.lens)}
                        aria-label={
                          isSaved(message.content, message.lens)
                            ? t('ask.saved.already')
                            : t('ask.saved.save')
                        }
                        className="-my-2 -mr-2 inline-flex size-11 shrink-0 items-center justify-center rounded-control text-ink-muted hover:bg-surface disabled:text-neel"
                      >
                        <Bookmark
                          className="size-4"
                          aria-hidden
                          fill={isSaved(message.content, message.lens) ? 'currentColor' : 'none'}
                        />
                      </button>
                    ) : null}
                  </div>
                </li>
              ) : (
                <li key={message.id}>
                  <AnswerView
                    message={message}
                    onNextQuestion={(q) => ask(q)}
                    onShowWork={() => setWork(message)}
                    onRetry={() => {
                      const asked = current.messages[i - 1]
                      if (asked?.role === 'user') ask(asked.content)
                    }}
                  />
                </li>
              ),
            )}
          </ol>
        ) : null}

        <div className="mt-6 pb-4">
          {current ? (
            <div className="mb-3 max-w-conversation">
              <LensPicker value={activeLens} onChange={(next) => changeLens.mutate(next)} />
            </div>
          ) : null}
          {failure ? (
            <FormAlert tone="error" className="mb-3 max-w-conversation">
              {askError(t, failure)}
            </FormAlert>
          ) : null}
          <AskBox
            ref={box}
            onAsk={(q) => ask(q)}
            busy={busy}
            disabled={working}
            placeholder={current ? t('ask.followUpPlaceholder') : undefined}
            initial={typed}
          />
        </div>
      </div>

      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent title={t('ask.history')}>{sidebar(() => setHistoryOpen(false))}</SheetContent>
      </Sheet>
      <ShowMyWork
        message={work}
        open={work !== null}
        onOpenChange={(open) => !open && setWork(null)}
      />
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t('ask.deleteTitle')}
        description={t('ask.deleteBody')}
        confirmLabel={t('ask.deleteConfirm')}
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </div>
  )
}
