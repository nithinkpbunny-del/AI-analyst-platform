import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { authApi } from '../../lib/authApi'
import { formatRelativeTime } from '../../lib/format'

const KEY = ['auth', 'sessions'] as const

/** The browsers where you are signed in, with "Sign out" for each and for all the others. */
export function DevicesSection() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const sessions = useQuery({ queryKey: KEY, queryFn: authApi.sessions })
  const [notice, setNotice] = useState<string | null>(null)
  const refresh = () => client.invalidateQueries({ queryKey: KEY })
  const revoke = useMutation({ mutationFn: authApi.revokeSession, onSuccess: refresh })
  const revokeOthers = useMutation({
    mutationFn: authApi.revokeOtherSessions,
    onSuccess: (result) => {
      setNotice(t('security.signedOutOthers', { count: result.sessions_revoked }))
      void refresh()
    },
  })
  const items = sessions.data?.sessions ?? []
  const others = items.filter((item) => !item.current)

  return (
    <section
      aria-labelledby="devices-title"
      className="rounded-panel border border-line bg-surface p-4 tablet:p-6"
    >
      <h2 id="devices-title" className="text-h4">
        {t('security.devices')}
      </h2>
      <p className="m-0 mt-2 text-small text-ink-muted">{t('security.devicesIntro')}</p>
      {notice ? (
        <FormAlert tone="success" className="mt-4">
          {notice}
        </FormAlert>
      ) : null}
      {sessions.isPending ? (
        <p role="status" className="m-0 mt-4 text-small text-ink-muted">
          {t('security.loading')}
        </p>
      ) : null}
      {sessions.isError ? (
        <FormAlert tone="error" className="mt-4">
          {t('security.loadFailed')}
        </FormAlert>
      ) : null}
      {items.length ? (
        <ul className="m-0 mt-4 list-none p-0">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex min-h-11 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line py-2 last:border-b-0"
            >
              <div className="flex min-w-0 flex-col">
                <span className="text-small font-medium text-ink">
                  {item.browser}
                  {item.current ? (
                    <span className="ml-2 rounded-pill bg-neel-soft px-2 text-caption text-neel">
                      {t('security.thisDevice')}
                    </span>
                  ) : null}
                </span>
                <span className="text-caption text-ink-muted tabular-nums">
                  {t('security.lastActive', { when: formatRelativeTime(item.last_seen_at) })}
                  {item.ip ? ` · ${t('security.ip', { ip: item.ip })}` : ''}
                </span>
              </div>
              {!item.current ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={revoke.isPending}
                  aria-label={t('security.signOutDeviceLabel', { browser: item.browser })}
                  onClick={() => revoke.mutate(item.id)}
                >
                  {t('security.signOutDevice')}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {sessions.isSuccess ? (
        others.length ? (
          <Button
            variant="secondary"
            className="mt-4"
            disabled={revokeOthers.isPending}
            onClick={() => revokeOthers.mutate()}
          >
            {t('security.signOutOthers')}
          </Button>
        ) : (
          <p className="m-0 mt-4 text-small text-ink-muted">{t('security.noOtherDevices')}</p>
        )
      ) : null}
    </section>
  )
}
