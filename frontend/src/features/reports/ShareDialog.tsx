import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { reportsApi, type ShareObject } from '../../lib/reportsApi'

/**
 * Share an answer or a report with people in the workspace (BUILD_SPEC X3). People whose connection access does not
 * cover the data it uses are listed but cannot be chosen; the server checks the same rule.
 */
export function ShareDialog({
  kind,
  objectId,
  open,
  onOpenChange,
}: {
  kind: ShareObject
  objectId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const key = ['shares', kind, objectId]
  const list = useQuery({
    queryKey: key,
    queryFn: () => reportsApi.shares(kind, objectId),
    enabled: open,
  })
  const [chosen, setChosen] = useState<string[]>([])
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const share = useMutation({
    mutationFn: () => reportsApi.share(kind, objectId, chosen),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data)
      setChosen([])
      setNotice({ tone: 'success', text: t('share.done') })
    },
    onError: (error) => setNotice({ tone: 'error', text: error.message }),
  })
  const unshare = useMutation({
    mutationFn: (shareId: string) => reportsApi.unshare(shareId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  })
  const toggle = (userId: string, on: boolean) =>
    setChosen((now) => (on ? [...now, userId] : now.filter((u) => u !== userId)))

  const candidates = (list.data?.candidates ?? []).filter((c) => !c.shared)
  const shares = list.data?.shares ?? []
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setNotice(null)
          setChosen([])
        }
        onOpenChange(next)
      }}
    >
      <DialogContent title={t('share.title')} description={t('share.intro')}>
        {notice ? (
          <div className="mt-4">
            <FormAlert tone={notice.tone}>{notice.text}</FormAlert>
          </div>
        ) : null}
        <fieldset className="m-0 mt-4 border-0 p-0">
          <legend className="text-small font-semibold">{t('share.withLabel')}</legend>
          {list.isSuccess && candidates.length === 0 && shares.length === 0 ? (
            <p className="m-0 mt-2 text-small text-ink-muted">{t('share.noCandidates')}</p>
          ) : null}
          <ul className="m-0 mt-2 list-none p-0">
            {candidates.map((person) => {
              const inputId = `share-${person.user_id}`
              return (
                <li
                  key={person.user_id}
                  className="flex min-h-11 items-center gap-3 border-b border-line"
                >
                  <input
                    id={inputId}
                    type="checkbox"
                    className="size-5 accent-neel"
                    disabled={!person.can_see}
                    checked={chosen.includes(person.user_id)}
                    onChange={(event) => toggle(person.user_id, event.target.checked)}
                    aria-describedby={person.can_see ? undefined : `${inputId}-note`}
                  />
                  <label htmlFor={inputId} className="flex min-w-0 flex-1 flex-col py-2 text-small">
                    <span className="truncate">
                      {person.name ?? person.email}{' '}
                      <span className="text-ink-muted">· {t(`share.roles.${person.role}`)}</span>
                    </span>
                    {person.can_see ? null : (
                      <span id={`${inputId}-note`} className="text-caption text-caution">
                        {t('share.cannotSee')}
                      </span>
                    )}
                  </label>
                </li>
              )
            })}
          </ul>
        </fieldset>
        <Button
          className="mt-4"
          disabled={chosen.length === 0 || share.isPending}
          onClick={() => share.mutate()}
        >
          {share.isPending ? t('share.submitting') : t('share.submit')}
        </Button>
        <h3 className="m-0 mt-6 text-small font-semibold">{t('share.sharedWith')}</h3>
        {shares.length === 0 ? (
          <p className="m-0 mt-2 text-small text-ink-muted">{t('share.nobody')}</p>
        ) : (
          <ul className="m-0 mt-2 list-none p-0">
            {shares.map((entry) => (
              <li
                key={entry.id}
                className="flex min-h-11 items-center justify-between gap-3 border-b border-line text-small"
              >
                <span className="min-w-0 truncate">{entry.name ?? entry.email}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={unshare.isPending}
                  onClick={() => unshare.mutate(entry.id)}
                  aria-label={`${t('share.stop')}: ${entry.name ?? entry.email}`}
                >
                  {t('share.stop')}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  )
}
