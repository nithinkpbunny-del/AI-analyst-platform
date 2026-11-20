import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import type { AiPlan } from '../../lib/aiApi'
import { billingApi, type BillingPlan } from '../../lib/billingApi'
import { formatDate } from '../../lib/format'
import './strings'

/**
 * The plan change flow (BUILD_SPEC R7, M18): read what changes, then confirm. The preview changes nothing; the
 * confirm uses the same rules. Nothing is charged: billing is not switched on.
 */
export default function ChangePlanDialog({
  target,
  plans,
  onClose,
  onDone,
}: {
  target: AiPlan
  plans: BillingPlan[]
  onClose: () => void
  onDone: (text: string) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const name = plans.find((p) => p.key === target)?.name ?? target
  const preview = useQuery({
    queryKey: ['billing-preview', target],
    queryFn: () => billingApi.preview(target),
    gcTime: 0,
    staleTime: 0,
    retry: false,
  })
  const confirm = useMutation({
    mutationFn: () => billingApi.confirm(target),
    onSuccess: async (data) => {
      queryClient.setQueryData(['billing'], data)
      await queryClient.invalidateQueries({ queryKey: ['ai-settings'] })
      await queryClient.invalidateQueries({ queryKey: ['usage'] })
      await queryClient.invalidateQueries({ queryKey: ['billing-summary'] })
      await queryClient.invalidateQueries({ queryKey: ['today'] })
      onDone(t('billing.change.done', { plan: name }))
      onClose()
    },
  })
  const data = preview.data
  const effectText = (effect: { code: string } & Record<string, unknown>) =>
    t(`billing.change.effects.${effect.code}`, {
      provider: String(effect.provider ?? ''),
      last4: String(effect.last4 ?? ''),
      limit: Number(effect.limit ?? 0),
      used: Number(effect.used ?? 0),
      date: effect.resets_on ? formatDate(String(effect.resets_on), 'UTC') : '',
    })
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent
        title={t('billing.change.title', { plan: name })}
        description={t('billing.change.intro')}
      >
        {preview.isPending ? (
          <p className="m-0 mt-4 text-body text-ink-muted">{t('billing.change.loading')}</p>
        ) : null}
        {preview.isError ? (
          <FormAlert tone="error" className="mt-4">
            {t('billing.change.previewFailed')}
          </FormAlert>
        ) : null}
        {confirm.isError ? (
          <FormAlert tone="error" className="mt-4">
            {confirm.error.message}
          </FormAlert>
        ) : null}
        {data ? (
          <div className="mt-4 flex flex-col gap-4">
            {!data.allowed ? (
              <FormAlert tone="error">
                <strong className="block">{t('billing.change.blocked')}</strong>
                {data.blockers.map((code) => (
                  <span key={code} className="block">
                    {t(`billing.change.blockers.${code}`, { defaultValue: code })}
                  </span>
                ))}
              </FormAlert>
            ) : null}
            <ul className="m-0 list-none p-0">
              {data.effects.map((effect) => (
                <li
                  key={effect.code}
                  className="border-b border-line py-2 text-body last:border-b-0"
                >
                  {effectText(effect)}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="secondary" onClick={onClose}>
                {t('dialog.cancel')}
              </Button>
              <Button
                type="button"
                disabled={!data.allowed || confirm.isPending}
                onClick={() => confirm.mutate()}
              >
                {confirm.isPending
                  ? t('billing.change.confirming')
                  : t('billing.change.confirm', { plan: name })}
              </Button>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
