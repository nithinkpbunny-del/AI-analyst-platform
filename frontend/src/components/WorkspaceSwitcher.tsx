import { Check, ChevronsUpDown } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { ApiError } from '../lib/api'
import { useAuth, useSession } from '../lib/authContext'
import { cn } from '../lib/cn'
import { FormAlert } from './FormAlert'
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover'

function Initial({ name }: { name: string }) {
  return (
    <span
      aria-hidden
      className="flex size-8 shrink-0 items-center justify-center rounded-control bg-neel-soft font-display text-small font-semibold text-neel"
    >
      {name.trim().charAt(0).toUpperCase() || '·'}
    </span>
  )
}

/**
 * The current workspace, and a way to move to another one the person belongs to. With a single workspace it is
 * just a label. `compact`: the tablet rail and the collapsed sidebar show only the initial.
 */
export function WorkspaceSwitcher({
  compact = false,
  className,
}: {
  compact?: boolean
  className?: string
}) {
  const { t } = useTranslation()
  const session = useSession()
  const { switchWorkspace } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const current = session.organization
  const many = session.memberships.length > 1

  const label = (
    <>
      <Initial name={current.name} />
      <span className={cn('min-w-0 flex-1 truncate text-left', compact && 'sr-only')}>
        <span className="block truncate text-small font-semibold text-ink">{current.name}</span>
        <span className="block truncate text-caption text-ink-muted">
          {t(`account.roles.${session.role}`)}
        </span>
      </span>
    </>
  )

  if (!many) {
    return (
      <div
        className={cn('flex min-h-11 items-center gap-2 px-2', className)}
        title={compact ? current.name : undefined}
      >
        {label}
      </div>
    )
  }

  const choose = async (organizationId: string) => {
    if (organizationId === current.id) {
      setOpen(false)
      return
    }
    setBusy(organizationId)
    setProblem(null)
    try {
      await switchWorkspace(organizationId)
      setOpen(false)
      void navigate('/today')
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : t('workspace.switchFailed'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          'flex min-h-11 w-full items-center gap-2 rounded-control px-2 hover:bg-neel-soft',
          compact && 'justify-center',
          className,
        )}
        data-testid="workspace-switcher"
      >
        {label}
        {/* Part of the button's name, after the visible text (WCAG 2.5.3 label in name). */}
        <span className="sr-only">{t('workspace.switchHint')}</span>
        {!compact ? (
          <ChevronsUpDown className="size-4 shrink-0 text-ink-muted" aria-hidden />
        ) : null}
      </PopoverTrigger>
      <PopoverContent align="start" className="p-2">
        <p className="m-0 px-2 pt-1 pb-2 text-caption text-ink-muted">{t('workspace.title')}</p>
        <ul className="m-0 list-none p-0" aria-label={t('workspace.title')}>
          {session.memberships.map((membership) => {
            const active = membership.organization_id === current.id
            return (
              <li key={membership.organization_id}>
                <button
                  type="button"
                  aria-current={active ? 'true' : undefined}
                  disabled={busy !== null}
                  onClick={() => void choose(membership.organization_id)}
                  className={cn(
                    'flex min-h-11 w-full items-center gap-2 rounded-control px-2 text-left hover:bg-neel-soft disabled:opacity-50',
                    active && 'bg-neel-soft',
                  )}
                >
                  <Initial name={membership.organization_name} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-small font-medium text-ink">
                      {membership.organization_name}
                    </span>
                    <span className="block text-caption text-ink-muted">
                      {t(`account.roles.${membership.role}`)}
                    </span>
                  </span>
                  {active ? <Check className="size-4 shrink-0 text-neel" aria-hidden /> : null}
                </button>
              </li>
            )
          })}
        </ul>
        {problem ? (
          <FormAlert tone="error" className="mt-2">
            {problem}
          </FormAlert>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
