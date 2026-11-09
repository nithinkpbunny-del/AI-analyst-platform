import { Copy, Download } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'
import { CheckboxField } from '../../components/ui/input'

function download(text: string) {
  if (typeof URL.createObjectURL !== 'function') return
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'analyst-recovery-codes.txt'
  link.click()
  URL.revokeObjectURL(url)
}

/** Shows new recovery codes once, with copy/download, and asks the person to confirm they saved them. */
export function RecoveryCodes({
  codes,
  email,
  onDone,
  doneLabel,
}: {
  codes: string[]
  email: string
  onDone: () => void
  doneLabel?: string
}) {
  const { t } = useTranslation()
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | undefined>()
  const [copied, setCopied] = useState(false)
  const text = [t('setup.fileHeader', { email }), '', ...codes, ''].join('\n')
  return (
    <div className="flex flex-col gap-5">
      <p className="m-0 max-w-prose text-body">{t('setup.recoveryIntro')}</p>
      <ul
        aria-label={t('setup.recoveryList')}
        className="m-0 grid list-none grid-cols-2 gap-x-6 rounded-sheet border border-line bg-surface p-4 tabular-nums"
      >
        {codes.map((code) => (
          <li key={code} className="flex min-h-9 items-center text-body tracking-wide">
            {code}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-3">
        <Button
          variant="secondary"
          onClick={() => {
            void navigator.clipboard?.writeText(text).then(() => setCopied(true))
          }}
        >
          <Copy aria-hidden />
          {copied ? t('setup.copied') : t('setup.copyCodes')}
        </Button>
        <Button variant="secondary" onClick={() => download(text)}>
          <Download aria-hidden />
          {t('setup.downloadCodes')}
        </Button>
      </div>
      <CheckboxField
        name="saved"
        label={t('setup.saved')}
        checked={saved}
        onChange={(value) => {
          setSaved(value)
          if (value) setError(undefined)
        }}
        error={error}
      />
      <Button
        className="w-full tablet:w-fit"
        onClick={() => (saved ? onDone() : setError(t('setup.savedRequired')))}
      >
        {doneLabel ?? t('setup.done')}
      </Button>
    </div>
  )
}
