import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { cn } from '../../lib/cn'
import { getHealth, getReadiness } from '../../lib/api'

export function SystemStatus() {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['health'],
    queryFn: async ({ signal }) => {
      const [health, readiness] = await Promise.all([getHealth(signal), getReadiness(signal)])
      return { health, readiness }
    },
    retry: false,
  })

  if (query.isPending) return <p role="status">{t('settings.checking')}</p>
  if (query.isError) return <p role="alert">{t('settings.unreachable')}</p>
  const { health, readiness } = query.data
  return (
    <>
      <p role="status" className="m-0">
        {t('settings.connected', { version: health.version, env: health.env })}
      </p>
      <ul aria-label="Service checks" className="m-0 mt-3 list-none p-0">
        {Object.entries(readiness.checks ?? {}).map(([name, result]) => (
          <li
            key={name}
            className="flex min-h-11 items-center justify-between border-t border-line text-small last:border-b"
          >
            <span className="capitalize">{name}</span>
            <span className={cn('font-medium', result === 'ok' ? 'text-gain' : 'text-loss')}>
              {result === 'ok' ? t('settings.ready') : t('settings.notReady')}
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}
