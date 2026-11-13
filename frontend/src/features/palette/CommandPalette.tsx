import { useQuery } from '@tanstack/react-query'
import { Inbox, MessageSquareText, Plug } from 'lucide-react'
import { useMemo, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { mainNav, visibleAdminNav } from '../../app/nav'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { useSession } from '../../lib/authContext'
import { chatApi } from '../../lib/chatApi'
import { cn } from '../../lib/cn'
import { connectionsApi } from '../../lib/connectionsApi'
import { useCan } from '../../lib/permissions'
import { askLink } from '../today/links'

interface Item {
  id: string
  group: 'screens' | 'saved' | 'connections'
  label: string
  to: string
  icon: typeof Inbox
}

/** Ctrl/Cmd + K: jump to a screen, a saved question or a connection (BUILD_SPEC 11a.8). */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const session = useSession()
  const canSave = useCan('saved_questions')
  const canSeeConnections = useCan('connections.view')
  const [text, setText] = useState('')
  const [active, setActive] = useState(0)
  const saved = useQuery({
    queryKey: ['saved-questions'],
    queryFn: chatApi.saved,
    enabled: open && canSave,
  })
  const connections = useQuery({
    queryKey: ['connections'],
    queryFn: connectionsApi.list,
    enabled: open && canSeeConnections,
  })

  const items = useMemo<Item[]>(() => {
    const screens: Item[] = [...mainNav, ...visibleAdminNav(session.permissions)].map((nav) => ({
      id: `screen-${nav.key}`,
      group: 'screens',
      label: t(`nav.${nav.key}`),
      to: nav.path,
      icon: nav.icon,
    }))
    screens.push({
      id: 'screen-shared',
      group: 'screens',
      label: t('palette.shared'),
      to: '/shared',
      icon: Inbox,
    })
    const questions: Item[] = (saved.data ?? []).map((q) => ({
      id: `saved-${q.id}`,
      group: 'saved',
      label: q.text,
      to: askLink(q.text),
      icon: MessageSquareText,
    }))
    const sources: Item[] = (connections.data?.connections ?? []).map((c) => ({
      id: `connection-${c.id}`,
      group: 'connections',
      label: c.name,
      to: `/connections/${c.id}`,
      icon: Plug,
    }))
    return [...screens, ...questions, ...sources]
  }, [t, session.permissions, saved.data, connections.data])

  const query = text.trim().toLowerCase()
  const shown = query ? items.filter((item) => item.label.toLowerCase().includes(query)) : items
  const go = (item: Item | undefined) => {
    if (!item) return
    onOpenChange(false)
    setText('')
    void navigate(item.to)
  }
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((i) => Math.min(i + 1, shown.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      go(shown[active])
    }
  }
  const groups: Item['group'][] = ['screens', 'saved', 'connections']
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setText('')
        onOpenChange(next)
      }}
    >
      <DialogContent title={t('palette.label')} className="max-w-lg">
        <input
          autoFocus
          role="combobox"
          aria-expanded
          aria-controls="palette-list"
          aria-activedescendant={shown[active]?.id}
          aria-label={t('palette.placeholder')}
          placeholder={t('palette.placeholder')}
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          className="mt-4 min-h-11 w-full rounded-control border border-line-strong bg-surface px-3 text-body text-ink placeholder:text-ink-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-neel"
        />
        <div
          id="palette-list"
          role="listbox"
          aria-label={t('palette.label')}
          className="mt-3 max-h-80 overflow-y-auto"
        >
          {shown.length === 0 ? (
            <p className="m-0 p-3 text-small text-ink-muted">{t('palette.empty')}</p>
          ) : null}
          {groups.map((group) => {
            const inGroup = shown.filter((item) => item.group === group)
            if (inGroup.length === 0) return null
            return (
              <div key={group} role="group" aria-label={t(`palette.${group}`)}>
                <p className="m-0 px-3 pt-2 pb-1 text-caption text-ink-muted">
                  {t(`palette.${group}`)}
                </p>
                {inGroup.map((item) => {
                  const index = shown.indexOf(item)
                  const Icon = item.icon
                  return (
                    <div
                      key={item.id}
                      id={item.id}
                      role="option"
                      aria-selected={index === active}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => go(item)}
                      className={cn(
                        'flex min-h-11 cursor-pointer items-center gap-3 rounded-control px-3 text-small',
                        index === active ? 'bg-neel-soft text-ink' : 'text-ink',
                      )}
                    >
                      <Icon
                        className="size-4 shrink-0 text-ink-muted"
                        aria-hidden
                        strokeWidth={1.5}
                      />
                      <span className="truncate">{item.label}</span>
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}
