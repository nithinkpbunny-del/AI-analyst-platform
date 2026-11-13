import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { QualityBanner } from '../../components/QualityBanner'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { ApiError, saveFile } from '../../lib/api'
import { useSession } from '../../lib/authContext'
import { formatDate } from '../../lib/format'
import { useCan } from '../../lib/permissions'
import { teamApi } from '../../lib/teamApi'

const ORGANIZATION = ['organization']

/** Download the workspace's data, or delete it after 7 days (BUILD_SPEC 6.6). Owners only. */
export function PrivacySection() {
  const { t } = useTranslation()
  const { organization } = useSession()
  const canManage = useCan('org.manage')
  const queryClient = useQueryClient()
  const org = useQuery({
    queryKey: ORGANIZATION,
    queryFn: teamApi.organization,
    enabled: canManage,
  })
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const [fieldError, setFieldError] = useState<string | undefined>()
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)

  const download = useMutation({
    mutationFn: teamApi.exportData,
    onSuccess: saveFile,
    onError: (error) => setNotice({ tone: 'error', text: error.message }),
  })
  const remove = useMutation({
    mutationFn: () => teamApi.requestDeletion(typed),
    onSuccess: (data) => {
      queryClient.setQueryData(ORGANIZATION, data)
      setConfirming(false)
      setTyped('')
    },
    onError: (error) => {
      if (error instanceof ApiError && error.fields.confirm_name)
        setFieldError(error.fields.confirm_name)
      else setNotice({ tone: 'error', text: error.message })
    },
  })
  const keep = useMutation({
    mutationFn: teamApi.cancelDeletion,
    onSuccess: (data) => {
      queryClient.setQueryData(ORGANIZATION, data)
      setNotice({ tone: 'success', text: t('privacy.cancelled') })
    },
    onError: (error) => setNotice({ tone: 'error', text: error.message }),
  })

  const due = org.data?.deletion_due_at
  return (
    <section
      aria-labelledby="privacy-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="privacy-title" className="text-h4">
        {t('privacy.title')}
      </h2>
      <p className="m-0 mt-2 text-small text-ink-muted">{t('privacy.intro')}</p>
      {!canManage ? (
        <p className="m-0 mt-4 text-small text-ink-muted">{t('privacy.ownersOnly')}</p>
      ) : (
        <div className="mt-4 flex flex-col gap-6">
          {notice ? <FormAlert tone={notice.tone}>{notice.text}</FormAlert> : null}
          <div>
            <h3 className="m-0 text-small font-semibold">{t('privacy.exportTitle')}</h3>
            <p className="m-0 mt-1 text-small text-ink-muted">{t('privacy.exportIntro')}</p>
            <Button
              variant="secondary"
              className="mt-3"
              disabled={download.isPending}
              onClick={() => {
                setNotice(null)
                download.mutate()
              }}
            >
              {download.isPending ? t('privacy.exporting') : t('privacy.export')}
            </Button>
          </div>
          <div>
            <h3 className="m-0 text-small font-semibold">{t('privacy.deleteTitle')}</h3>
            {due ? (
              <>
                <QualityBanner className="mt-2">
                  {t('privacy.scheduled', { date: formatDate(due) })}
                </QualityBanner>
                <Button className="mt-3" disabled={keep.isPending} onClick={() => keep.mutate()}>
                  {t('privacy.cancel')}
                </Button>
              </>
            ) : (
              <>
                <p className="m-0 mt-1 text-small text-ink-muted">{t('privacy.deleteIntro')}</p>
                <Button variant="secondary" className="mt-3" onClick={() => setConfirming(true)}>
                  {t('privacy.delete')}
                </Button>
              </>
            )}
          </div>
        </div>
      )}
      <ConfirmDialog
        open={confirming}
        onOpenChange={(open) => {
          setConfirming(open)
          if (!open) {
            setTyped('')
            setFieldError(undefined)
          }
        }}
        title={t('privacy.confirmTitle', { name: organization.name })}
        description={t('privacy.confirmBody')}
        confirmLabel={t('privacy.confirm')}
        busy={remove.isPending || typed.trim().toLowerCase() !== organization.name.toLowerCase()}
        onConfirm={() => remove.mutate()}
      >
        <Field
          className="mt-4"
          label={t('privacy.confirmLabel')}
          name="confirm-name"
          autoComplete="off"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          error={fieldError}
        />
      </ConfirmDialog>
    </section>
  )
}
