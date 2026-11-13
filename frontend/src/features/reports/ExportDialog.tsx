import { useMutation } from '@tanstack/react-query'
import { FileSpreadsheet, FileText } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { saveFile } from '../../lib/api'
import { reportsApi, type ExportFormat, type ShareTarget } from '../../lib/reportsApi'

/** "Export PDF" / "Export Excel" for an answer, a report or an item shared with me (BUILD_SPEC X3). */
export function ExportDialog({
  target,
  open,
  onOpenChange,
}: {
  target: ShareTarget
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const download = useMutation({
    mutationFn: (format: ExportFormat) => reportsApi.download(target, format),
    onSuccess: (file) => {
      saveFile(file)
      onOpenChange(false)
    },
  })
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) download.reset()
        onOpenChange(next)
      }}
    >
      <DialogContent title={t('exportDialog.title')} description={t('exportDialog.intro')}>
        {download.isError ? (
          <div className="mt-4">
            <FormAlert tone="error">{t('exportDialog.error')}</FormAlert>
          </div>
        ) : null}
        <div className="mt-6 flex flex-wrap gap-2">
          <Button disabled={download.isPending} onClick={() => download.mutate('pdf')}>
            <FileText aria-hidden />
            {t('exportDialog.pdf')}
          </Button>
          <Button
            variant="secondary"
            disabled={download.isPending}
            onClick={() => download.mutate('xlsx')}
          >
            <FileSpreadsheet aria-hidden />
            {t('exportDialog.xlsx')}
          </Button>
        </div>
        {download.isPending ? (
          <p role="status" className="m-0 mt-3 text-small text-ink-muted">
            {t('exportDialog.working')}
          </p>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
