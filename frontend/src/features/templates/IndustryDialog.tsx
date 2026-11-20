import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { cn } from '../../lib/cn'
import { teamApi, type Industry } from '../../lib/teamApi'
import './strings'
import { TemplatePreview } from './TemplatePreview'
import { findTemplate, useTemplates } from './useTemplates'

/**
 * Onboarding step "Company details" (BUILD_SPEC 11a.8, X9): choose the kind of business and see what the template
 * sets up. Loaded on demand from Today, so it adds nothing to the app shell.
 */
export default function IndustryDialog({
  open,
  onOpenChange,
  current,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  current: Industry | null
  onSaved: (text: string) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const templates = useTemplates(open)
  const [chosen, setChosen] = useState<Industry | null>(current)
  const selected = findTemplate(templates.data, chosen)
  const save = useMutation({
    mutationFn: (industry: Industry) => teamApi.updateOrganization({ industry }),
    onSuccess: async (data) => {
      queryClient.setQueryData(['organization'], data)
      await queryClient.invalidateQueries({ queryKey: ['today'] })
      onSaved(t('templates.picker.saved'))
      onOpenChange(false)
    },
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={t('templates.picker.title')}
        description={t('templates.picker.intro')}
        className="max-w-xl"
      >
        {templates.isError ? (
          <FormAlert tone="error" className="mt-4">
            {t('templates.loadFailed')}
          </FormAlert>
        ) : null}
        {templates.isPending ? (
          <p className="m-0 mt-4 text-body text-ink-muted">{t('templates.loading')}</p>
        ) : null}
        {templates.data ? (
          <form
            className="mt-4 flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault()
              if (chosen) save.mutate(chosen)
            }}
          >
            {save.isError ? <FormAlert tone="error">{save.error.message}</FormAlert> : null}
            <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
              <legend className="mb-2 text-small font-semibold">
                {t('templates.picker.choose')}
              </legend>
              <div className="grid grid-cols-1 gap-2 tablet:grid-cols-2">
                {templates.data.map((template) => (
                  <label
                    key={template.key}
                    className={cn(
                      'flex min-h-11 cursor-pointer items-center gap-3 rounded-control border px-3 py-2 text-body',
                      chosen === template.key
                        ? 'border-neel bg-neel-soft'
                        : 'border-line-strong hover:bg-neel-soft',
                    )}
                  >
                    <input
                      type="radio"
                      name="industry"
                      value={template.key}
                      checked={chosen === template.key}
                      onChange={() => setChosen(template.key)}
                      className="size-4 accent-neel"
                    />
                    {template.name}
                  </label>
                ))}
              </div>
            </fieldset>
            {selected ? <TemplatePreview template={selected} /> : null}
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
                {t('dialog.cancel')}
              </Button>
              <Button type="submit" disabled={!chosen || save.isPending}>
                {save.isPending ? t('templates.picker.saving') : t('templates.picker.save')}
              </Button>
            </div>
          </form>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
