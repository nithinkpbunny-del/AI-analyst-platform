import { ArrowUp, LoaderCircle } from 'lucide-react'
import { forwardRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'

const MAX = 2000

/** The question box: Enter asks, Shift+Enter adds a line (BUILD_SPEC 11a.8). */
export const AskBox = forwardRef<
  HTMLTextAreaElement,
  {
    onAsk: (question: string) => void
    busy?: boolean
    disabled?: boolean
    placeholder?: string
    initial?: string
  }
>(function AskBox({ onAsk, busy = false, disabled = false, placeholder, initial = '' }, ref) {
  const { t } = useTranslation()
  const [text, setText] = useState(initial)
  const question = text.trim()

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!question || busy || disabled) return
    onAsk(question)
    setText('')
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form onSubmit={submit} className="flex max-w-conversation flex-col gap-1">
      <label htmlFor="ask-box" className="sr-only">
        {t('ask.label')}
      </label>
      <div className="flex items-end gap-2 rounded-control border border-line-strong bg-surface p-2 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-neel">
        <textarea
          id="ask-box"
          ref={ref}
          value={text}
          maxLength={MAX}
          rows={1}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder ?? t('ask.placeholder')}
          aria-describedby="ask-hint"
          disabled={disabled}
          className="max-h-48 min-h-11 flex-1 resize-none border-0 bg-transparent px-2 py-2.5 text-body text-ink outline-none [field-sizing:content] placeholder:text-ink-muted"
        />
        <Button type="submit" disabled={!question || busy || disabled} aria-busy={busy}>
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden />
          ) : (
            <ArrowUp aria-hidden strokeWidth={1.5} />
          )}
          {busy ? t('ask.sending') : t('ask.send')}
        </Button>
      </div>
      <p id="ask-hint" className="m-0 hidden px-1 text-caption text-ink-muted tablet:block">
        {t('ask.hint')}
      </p>
    </form>
  )
})
