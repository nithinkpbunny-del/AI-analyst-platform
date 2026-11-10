import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogClose, DialogContent } from '../../components/ui/dialog'
import { CheckboxField } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { cn } from '../../lib/cn'
import { connectionsApi, type Connection, type PersonAccess } from '../../lib/connectionsApi'
import { ACCESS } from './keys'

function AccessDialog({
  person,
  connections,
  onClose,
  onSaved,
}: {
  person: PersonAccess | null
  connections: Connection[]
  onClose: () => void
  onSaved: (person: PersonAccess) => void
}) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<'all' | 'selected'>(
    person?.access === 'selected' ? 'selected' : 'all',
  )
  const [chosen, setChosen] = useState<Set<string>>(new Set(person?.connection_ids ?? []))
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => connectionsApi.setAccess(person?.membership_id ?? '', mode, [...chosen]),
    onSuccess: onSaved,
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('connections.access.loadFailed')),
  })
  if (!person) return null
  const toggle = (id: string, on: boolean) =>
    setChosen((current) => {
      const next = new Set(current)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        title={t('connections.access.dialogTitle', { name: person.name })}
        description={t('connections.access.dialogIntro', { name: person.name })}
      >
        {error ? (
          <FormAlert tone="error" className="mt-4">
            {error}
          </FormAlert>
        ) : null}
        <fieldset className="m-0 mt-4 flex flex-col gap-2 border-0 p-0">
          <legend className="sr-only">
            {t('connections.access.dialogTitle', { name: person.name })}
          </legend>
          {(['all', 'selected'] as const).map((value) => (
            <label
              key={value}
              className={cn(
                'flex min-h-11 cursor-pointer items-center gap-3 rounded-control border px-4 py-2 text-small',
                mode === value ? 'border-neel bg-neel-soft' : 'border-line-strong',
              )}
            >
              <input
                type="radio"
                name="access"
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
                className="accent-neel"
              />
              {value === 'all'
                ? t('connections.access.everything')
                : t('connections.access.onlyThese')}
            </label>
          ))}
        </fieldset>
        {mode === 'selected' ? (
          <fieldset className="m-0 mt-4 flex flex-col gap-3 border-0 p-0 pl-2">
            <legend className="sr-only">{t('connections.access.onlyThese')}</legend>
            {connections.length ? (
              connections.map((connection) => (
                <CheckboxField
                  key={connection.id}
                  name={`grant-${connection.id}`}
                  label={connection.name}
                  checked={chosen.has(connection.id)}
                  onChange={(on) => toggle(connection.id, on)}
                />
              ))
            ) : (
              <p className="m-0 text-small text-ink-muted">
                {t('connections.access.noConnections')}
              </p>
            )}
          </fieldset>
        ) : null}
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <DialogClose asChild>
            <Button variant="secondary">{t('dialog.cancel')}</Button>
          </DialogClose>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            {t('connections.access.save')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function accessText(t: TFunction, person: PersonAccess, total: number) {
  if (person.access === 'owner') return t('connections.access.owner')
  if (person.access === 'all') return t('connections.access.all')
  if (!person.connection_ids.length) return t('connections.access.none')
  return t('connections.access.some', { chosen: person.connection_ids.length, count: total })
}

/** Owners decide which connections each Analyst or Viewer sees (BUILD_SPEC R15, DECISIONS D50). */
export function AccessSection({ connections }: { connections: Connection[] }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const access = useQuery({ queryKey: ACCESS, queryFn: connectionsApi.access })
  const [editing, setEditing] = useState<PersonAccess | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const saved = (person: PersonAccess) => {
    setEditing(null)
    const what =
      person.access === 'all'
        ? t('connections.access.savedAll')
        : person.connection_ids.length
          ? t('connections.access.savedSome')
          : t('connections.access.savedNone')
    setNotice(t('connections.access.saved', { name: person.name, what }))
    void client.invalidateQueries({ queryKey: ACCESS })
  }
  return (
    <section
      aria-labelledby="access-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="access-title" className="text-h4">
        {t('connections.access.title')}
      </h2>
      <p className="m-0 mt-2 max-w-prose text-small text-ink-muted">
        {t('connections.access.intro')}
      </p>
      {notice ? (
        <FormAlert tone="success" className="mt-4">
          {notice}
        </FormAlert>
      ) : null}
      {access.isError ? (
        <FormAlert tone="error" className="mt-4">
          {t('connections.access.loadFailed')}
        </FormAlert>
      ) : null}
      {access.data ? (
        <ul className="m-0 mt-2 list-none p-0" aria-label={t('connections.access.title')}>
          {access.data.people.map((person) => (
            <li
              key={person.membership_id}
              className="flex min-h-11 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line py-3 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="m-0 truncate text-small font-medium text-ink">
                  {person.name}
                  <span className="ml-2 text-caption font-normal text-ink-muted">
                    {t(`account.roles.${person.role}`)}
                  </span>
                </p>
                <p className="m-0 truncate text-caption text-ink-muted">{person.email}</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-small text-ink">
                  {accessText(t, person, connections.length)}
                </span>
                {person.access !== 'owner' ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={t('connections.access.changeLabel', { name: person.name })}
                    onClick={() => setEditing(person)}
                  >
                    {t('connections.access.change')}
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {editing ? (
        <AccessDialog
          key={editing.membership_id}
          person={editing}
          connections={connections}
          onClose={() => setEditing(null)}
          onSaved={saved}
        />
      ) : null}
    </section>
  )
}
