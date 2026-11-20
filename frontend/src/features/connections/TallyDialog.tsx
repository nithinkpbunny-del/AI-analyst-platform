import './strings'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Copy } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { connectionsApi, type PairingCode } from '../../lib/connectionsApi'
import { CONNECTIONS } from './keys'

function timeOf(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit' }).format(
    new Date(value),
  )
}

/** The code, how long it works, and the three steps on the Tally PC. */
export function PairingSteps({ pairing }: { pairing: PairingCode }) {
  const { t } = useTranslation()
  const [expired, setExpired] = useState(false)
  const [copied, setCopied] = useState(false)
  const command = `tally-bridge pair ${pairing.code} --server ${pairing.server_url}`
  useEffect(() => {
    const check = () => setExpired(new Date(pairing.expires_at).getTime() <= Date.now())
    check()
    const timer = window.setInterval(check, 15_000)
    return () => window.clearInterval(timer)
  }, [pairing.expires_at])

  const copy = () => {
    void navigator.clipboard?.writeText(command).then(() => setCopied(true))
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="m-0 text-small text-ink-muted">{t('connections.tally.codeLabel')}</p>
        <p
          className="m-0 font-display text-h2 font-semibold tracking-wide tabular"
          data-testid="tally-code"
        >
          {pairing.code}
        </p>
        <p className="m-0 text-small text-ink-muted" role="status">
          {expired
            ? t('connections.tally.codeExpired')
            : t('connections.tally.codeExpires', { time: timeOf(pairing.expires_at) })}
        </p>
      </div>
      <ol className="m-0 flex flex-col gap-3 pl-5 text-small">
        <li>
          {t('connections.tally.step1')}{' '}
          {pairing.download_url ? (
            <a href={pairing.download_url} className="text-neel underline underline-offset-4">
              {t('connections.tally.step1Download')}
            </a>
          ) : (
            <span className="text-ink-muted">{t('connections.tally.step1Ask')}</span>
          )}
        </li>
        <li>{t('connections.tally.step2')}</li>
        <li>
          {t('connections.tally.step3')}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <code className="min-w-0 break-all rounded-control border border-line bg-paper px-3 py-2 text-small">
              {command}
            </code>
            <Button size="sm" variant="secondary" onClick={copy}>
              <Copy aria-hidden />
              {copied ? t('connections.tally.copied') : t('connections.tally.copy')}
            </Button>
          </div>
        </li>
        <li>{t('connections.tally.step4')}</li>
      </ol>
      <p className="m-0 text-caption text-ink-muted">{t('connections.tally.readOnlyNote')}</p>
    </div>
  )
}

/**
 * Connect Tally (a new connection with a pairing code), or a new pairing code for an existing Tally connection.
 * Loaded on demand (it is not part of the first load).
 */
export function TallyDialog({
  open,
  onOpenChange,
  connectionId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Set for a new code on this connection; empty to add a new Tally connection. */
  connectionId?: string
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [name, setName] = useState('')
  const [pairing, setPairing] = useState<PairingCode | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const fail = (error: unknown) =>
    setProblem(error instanceof ApiError ? error.message : t('connections.tally.failed'))

  const add = useMutation({
    mutationFn: () => connectionsApi.addTally(name.trim() || undefined),
    onSuccess: (result) => {
      setPairing(result.pairing)
      void client.invalidateQueries({ queryKey: CONNECTIONS })
    },
    onError: fail,
  })
  const renew = useMutation({
    mutationFn: () => connectionsApi.tallyPairingCode(connectionId ?? ''),
    onSuccess: setPairing,
    onError: fail,
  })
  const { mutate: getNewCode } = renew
  useEffect(() => {
    if (open && connectionId) getNewCode()
  }, [open, connectionId, getNewCode])

  const close = (next: boolean) => {
    if (!next) {
      setName('')
      setPairing(null)
      setProblem(null)
    }
    onOpenChange(next)
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    add.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={
          pairing
            ? t(connectionId ? 'connections.tally.newCodeTitle' : 'connections.tally.codeTitle')
            : t('connections.tally.title')
        }
        description={t('connections.tally.intro')}
      >
        <div className="mt-4 flex flex-col gap-4">
          {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
          {pairing ? (
            <>
              <PairingSteps pairing={pairing} />
              <div>
                <Button onClick={() => close(false)}>{t('connections.tally.done')}</Button>
              </div>
            </>
          ) : connectionId ? (
            <p role="status" className="m-0 text-small text-ink-muted">
              {t('connections.tally.gettingCode')}
            </p>
          ) : (
            <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
              <Field
                label={t('connections.tally.name')}
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={120}
                placeholder="Tally"
              />
              <div>
                <Button type="submit" disabled={add.isPending}>
                  {add.isPending
                    ? t('connections.tally.gettingCode')
                    : t('connections.tally.getCode')}
                </Button>
              </div>
            </form>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
