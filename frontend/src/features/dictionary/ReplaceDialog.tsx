import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogClose, DialogContent } from '../../components/ui/dialog'
import { SelectField } from '../../components/ui/select'
import { ApiError } from '../../lib/api'
import { connectionsApi } from '../../lib/connectionsApi'
import { DICTIONARY, dictionaryApi } from '../../lib/dictionaryApi'

/**
 * Replace a connection with another kind of source and keep the dictionary's mappings (R2). The new source is
 * added first as a normal connection; this moves what the dictionary knows over to it.
 */
export function ReplaceDialog({
  connection,
  open,
  onOpenChange,
}: {
  connection: { id: string; name: string }
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [target, setTarget] = useState('')
  const [removeOld, setRemoveOld] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const list = useQuery({ queryKey: ['connections'], queryFn: connectionsApi.list })
  // Only connections that have been read can take over: they show their tables.
  const candidates = (list.data?.connections ?? []).filter(
    (other) => other.id !== connection.id && other.datasets_count > 0,
  )
  const move = useMutation({
    mutationFn: () => dictionaryApi.replace(connection.id, target, removeOld),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: DICTIONARY })
      await client.invalidateQueries({ queryKey: ['connections'] })
      onOpenChange(false)
      void navigate('/dictionary')
    },
    onError: (caught) =>
      setError(
        caught instanceof ApiError
          ? t(`dictionary.replace.errors.${caught.code}`, { defaultValue: caught.message })
          : t('dictionary.replace.errors.generic'),
      ),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!target) return
    setError(null)
    move.mutate()
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={t('dictionary.replace.title', { name: connection.name })}
        description={t('dictionary.replace.intro')}
      >
        <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
          {candidates.length === 0 ? (
            <p className="m-0 text-small text-ink-muted">{t('dictionary.replace.none')}</p>
          ) : (
            <SelectField
              label={t('dictionary.replace.target')}
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            >
              <option value="">{t('dictionary.replace.choose')}</option>
              {candidates.map((other) => (
                <option key={other.id} value={other.id}>
                  {other.name}
                </option>
              ))}
            </SelectField>
          )}
          <label className="flex items-start gap-3 text-small">
            <input
              type="checkbox"
              className="mt-0.5 size-5"
              checked={removeOld}
              onChange={(event) => setRemoveOld(event.target.checked)}
            />
            <span>
              {t('dictionary.replace.removeOld', { name: connection.name })}
              {removeOld ? null : (
                <span className="block text-ink-muted">{t('dictionary.replace.keepOld')}</span>
              )}
            </span>
          </label>
          {error ? <FormAlert tone="error">{error}</FormAlert> : null}
          <div className="flex flex-wrap justify-end gap-2">
            <DialogClose asChild>
              <Button variant="secondary">{t('dialog.cancel')}</Button>
            </DialogClose>
            <Button type="submit" disabled={!target || move.isPending}>
              {move.isPending ? t('dictionary.replace.submitting') : t('dictionary.replace.submit')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
