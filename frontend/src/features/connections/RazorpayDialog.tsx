import './strings'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { connectionsApi, type Connection } from '../../lib/connectionsApi'
import { CONNECTIONS } from './keys'

const KEY_ID = /^rzp_(live|test)_[A-Za-z0-9]{4,}$/

/**
 * Connect Razorpay with an API key, or replace the key of an existing connection. The secret is write-only:
 * checked with Razorpay, stored encrypted, never shown again. Loaded on demand (it is not part of the first load).
 */
export function RazorpayDialog({
  open,
  onOpenChange,
  connection,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Set when replacing the key of this connection; empty when adding a new one. */
  connection?: Connection
  onDone: (message: string) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const replacing = connection !== undefined
  const [keyId, setKeyId] = useState('')
  const [secret, setSecret] = useState('')
  const [name, setName] = useState('')
  const [errors, setErrors] = useState<{ keyId?: string; secret?: string }>({})
  const [problem, setProblem] = useState<string | null>(null)

  const close = (next: boolean) => {
    if (!next) {
      setKeyId('')
      setSecret('')
      setName('')
      setErrors({})
      setProblem(null)
    }
    onOpenChange(next)
  }
  const save = useMutation({
    mutationFn: () => {
      const body = {
        key_id: keyId.trim(),
        key_secret: secret.trim(),
        ...(name.trim() ? { name: name.trim() } : {}),
      }
      return connection
        ? connectionsApi.replaceRazorpayKey(connection.id, body)
        : connectionsApi.addRazorpay(body)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: CONNECTIONS })
      onDone(t(replacing ? 'connections.razorpay.replaced' : 'connections.razorpay.connected'))
      close(false)
    },
    onError: (error) =>
      setProblem(error instanceof ApiError ? error.message : t('connections.razorpay.failed')),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const next = {
      keyId: KEY_ID.test(keyId.trim()) ? undefined : t('connections.razorpay.keyIdInvalid'),
      secret:
        secret.trim().length >= 8 && !/\s/.test(secret.trim())
          ? undefined
          : t('connections.razorpay.secretInvalid'),
    }
    setErrors(next)
    setProblem(null)
    if (!next.keyId && !next.secret) save.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={t(replacing ? 'connections.razorpay.replaceTitle' : 'connections.razorpay.title')}
        description={t('connections.razorpay.intro')}
      >
        <form onSubmit={submit} className="mt-4 flex flex-col gap-4" noValidate>
          {connection?.razorpay ? (
            <p className="m-0 text-small text-ink-muted">
              {t('connections.razorpay.current', { key: connection.razorpay.key_id_masked })}
            </p>
          ) : null}
          {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
          <Field
            label={t('connections.razorpay.keyId')}
            hint={t('connections.razorpay.keyIdHint')}
            error={errors.keyId}
            value={keyId}
            onChange={(event) => setKeyId(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            placeholder="rzp_live_…"
          />
          <Field
            label={t('connections.razorpay.keySecret')}
            hint={t('connections.razorpay.keySecretHint')}
            error={errors.secret}
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            type="password"
            revealable
            autoComplete="off"
            spellCheck={false}
          />
          {replacing ? null : (
            <Field
              label={t('connections.razorpay.name')}
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={120}
            />
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending
                ? t('connections.razorpay.checking')
                : t(replacing ? 'connections.razorpay.replace' : 'connections.razorpay.check')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
