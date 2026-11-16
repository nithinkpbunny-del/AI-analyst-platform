import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CreditCard, FileSpreadsheet, Sheet, Upload } from 'lucide-react'
import { lazy, Suspense, useId, useRef, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { ApiError } from '../../lib/api'
import { cn } from '../../lib/cn'
import { connectionsApi, uploadFile, type Connection, type Sources } from '../../lib/connectionsApi'
import { CONNECTIONS, fileProblem } from './keys'

// The key form loads when first opened: the Connections screen is part of the app's first load.
const RazorpayDialog = lazy(() =>
  import('./RazorpayDialog').then((m) => ({ default: m.RazorpayDialog })),
)

const ACCEPT =
  '.xlsx,.csv,.tsv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv'
function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.fields.file ?? error.message
  return fallback
}

/** A file picker plus drop zone. Used to add a file connection and to upload a new version of one. */
export function FileDrop({
  onFile,
  busy,
  label,
  hint,
}: {
  onFile: (file: File) => void
  busy: boolean
  label: string
  hint?: string
}) {
  const { t } = useTranslation()
  const input = useRef<HTMLInputElement>(null)
  const hintId = useId()
  const [over, setOver] = useState(false)
  const drop = (event: DragEvent) => {
    event.preventDefault()
    setOver(false)
    const file = event.dataTransfer.files[0]
    if (file) onFile(file)
  }
  return (
    <div
      onDragOver={(event) => {
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
      className={cn(
        'flex flex-wrap items-center gap-3 rounded-control border border-dashed px-4 py-3',
        over ? 'border-neel bg-neel-soft' : 'border-line-strong',
      )}
    >
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) onFile(file)
        }}
        data-testid="file-input"
      />
      <Button
        variant="secondary"
        disabled={busy}
        aria-describedby={hint ? hintId : undefined}
        onClick={() => input.current?.click()}
      >
        <Upload aria-hidden />
        {label}
      </Button>
      <span className="text-small text-ink-muted">{t('connections.add.drop')}</span>
      {hint ? (
        <p id={hintId} className="m-0 basis-full text-caption text-ink-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

export function AddData({ sources }: { sources: Sources }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [uploading, setUploading] = useState<string | null>(null)
  const [razorpayOpen, setRazorpayOpen] = useState(false)
  const upload = useMutation({
    mutationFn: (file: File) => uploadFile(file),
    onMutate: (file) => {
      setNotice(null)
      setUploading(file.name)
    },
    onSuccess: (created: Connection, file) => {
      setNotice({ tone: 'success', text: t('connections.add.uploaded', { name: file.name }) })
      void client.invalidateQueries({ queryKey: CONNECTIONS })
      return created
    },
    onError: (error) =>
      setNotice({ tone: 'error', text: errorText(error, t('connections.loadFailed')) }),
    onSettled: () => setUploading(null),
  })
  const google = useMutation({
    mutationFn: () => connectionsApi.googleStart(),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    onError: (error) =>
      setNotice({ tone: 'error', text: errorText(error, t('connections.loadFailed')) }),
  })
  const choose = (file: File) => {
    const problem = fileProblem(file, sources.upload_max_mb, t)
    if (problem) setNotice({ tone: 'error', text: problem })
    else upload.mutate(file)
  }
  const googleNote = !sources.google_sheets.available
    ? t('connections.add.googleOff')
    : sources.google_sheets.stand_in
      ? t('connections.add.googleStandIn')
      : t('connections.add.googleHint')

  return (
    <section
      aria-labelledby="add-data-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="add-data-title" className="text-h4">
        {t('connections.add.title')}
      </h2>
      <p className="m-0 mt-2 max-w-prose text-small text-ink-muted">{t('connections.add.intro')}</p>
      {notice ? (
        <FormAlert tone={notice.tone} className="mt-4">
          {notice.text}
        </FormAlert>
      ) : null}
      {uploading ? (
        <p role="status" className="m-0 mt-4 text-small text-ink">
          {t('connections.add.uploading', { name: uploading })}
        </p>
      ) : null}
      <div className="mt-4 grid grid-cols-1 gap-4 tablet:grid-cols-2 desktop:grid-cols-3">
        <div className="flex flex-col gap-3">
          <h3 className="flex items-center gap-2 font-sans text-body font-semibold tracking-normal">
            <FileSpreadsheet className="size-5 text-neel" aria-hidden strokeWidth={1.5} />
            {t('connections.add.uploadTitle')}
          </h3>
          <FileDrop
            busy={upload.isPending}
            onFile={choose}
            label={t('connections.add.choose')}
            hint={t('connections.add.uploadHint', { mb: sources.upload_max_mb })}
          />
        </div>
        <div className="flex flex-col gap-3">
          <h3 className="flex items-center gap-2 font-sans text-body font-semibold tracking-normal">
            <Sheet className="size-5 text-neel" aria-hidden strokeWidth={1.5} />
            {t('connections.add.googleTitle')}
          </h3>
          <div className="flex flex-col items-start gap-2">
            <Button
              disabled={!sources.google_sheets.available || google.isPending}
              onClick={() => google.mutate()}
            >
              {google.isPending ? t('connections.add.opening') : t('connections.add.connectGoogle')}
            </Button>
            <p className="m-0 text-caption text-ink-muted">{googleNote}</p>
          </div>
        </div>
        <div className="flex flex-col gap-3">
          <h3 className="flex items-center gap-2 font-sans text-body font-semibold tracking-normal">
            <CreditCard className="size-5 text-neel" aria-hidden strokeWidth={1.5} />
            {t('connections.add.razorpayTitle')}
          </h3>
          <div className="flex flex-col items-start gap-2">
            <Button disabled={!sources.razorpay.available} onClick={() => setRazorpayOpen(true)}>
              {t('connections.add.connectRazorpay')}
            </Button>
            <p className="m-0 text-caption text-ink-muted">
              {sources.razorpay.stand_in
                ? t('connections.add.razorpayStandIn')
                : t('connections.add.razorpayHint')}
            </p>
          </div>
        </div>
      </div>
      {razorpayOpen ? (
        <Suspense fallback={null}>
          <RazorpayDialog
            open={razorpayOpen}
            onOpenChange={setRazorpayOpen}
            onDone={(text) => setNotice({ tone: 'success', text })}
          />
        </Suspense>
      ) : null}
    </section>
  )
}
