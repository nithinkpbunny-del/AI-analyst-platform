import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '../../components/PageHeader'
import { cn } from '../../lib/cn'
import { useTheme, type ThemePreference } from '../../lib/theme'
import { SystemStatus } from './SystemStatus'

const options: { value: ThemePreference; key: string }[] = [
  { value: 'system', key: 'settings.themeSystem' },
  { value: 'light', key: 'settings.themeLight' },
  { value: 'dark', key: 'settings.themeDark' },
]

export function SettingsPage() {
  const { t } = useTranslation()
  const { theme, setTheme } = useTheme()
  useEffect(() => {
    document.title = `${t('pages.settings.title')} · ${t('app.name')}`
  }, [t])

  return (
    <>
      <PageHeader title={t('pages.settings.title')} intro={t('pages.settings.intro')} />
      <div className="grid gap-6 desktop:grid-cols-2">
        <section className="rounded-panel border border-line bg-surface p-4 tablet:p-6">
          <h2 className="text-h4">{t('settings.appearance')}</h2>
          <fieldset className="m-0 mt-4 border-0 p-0">
            <legend className="mb-2 text-small font-medium text-ink-muted">
              {t('settings.theme')}
            </legend>
            <div className="flex flex-wrap gap-2">
              {options.map((option) => (
                <label
                  key={option.value}
                  className={cn(
                    'flex min-h-11 cursor-pointer items-center gap-2 rounded-pill border px-4 text-small',
                    theme === option.value
                      ? 'border-neel bg-neel-soft text-neel'
                      : 'border-line-strong text-ink',
                  )}
                >
                  <input
                    type="radio"
                    name="theme"
                    value={option.value}
                    checked={theme === option.value}
                    onChange={() => setTheme(option.value)}
                    className="accent-neel"
                  />
                  {t(option.key)}
                </label>
              ))}
            </div>
          </fieldset>
        </section>
        <section className="rounded-panel border border-line bg-surface p-4 tablet:p-6">
          <h2 className="text-h4">{t('settings.systemStatus')}</h2>
          <div className="mt-4 text-small">
            <SystemStatus />
          </div>
        </section>
      </div>
    </>
  )
}
