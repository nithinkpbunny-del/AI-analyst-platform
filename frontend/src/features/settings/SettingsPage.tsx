import { lazy, Suspense, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { useAuth, useSession } from '../../lib/authContext'
import { useCan } from '../../lib/permissions'
import { cn } from '../../lib/cn'
import { useTheme, type ThemePreference } from '../../lib/theme'
import { AiPlanSection } from './AiPlanSection'
import { DevicesSection } from './DevicesSection'
import { PrivacySection } from './PrivacySection'
import { SecuritySection } from './SecuritySection'
import { SystemStatus } from './SystemStatus'
import { UsageSection } from './UsageSection'
import { WorkspaceSection } from './WorkspaceSection'

// Plan and billing (M18) loads on demand and only for Owners.
const BillingSection = lazy(() => import('../billing/BillingSection'))

const options: { value: ThemePreference; key: string }[] = [
  { value: 'system', key: 'settings.themeSystem' },
  { value: 'light', key: 'settings.themeLight' },
  { value: 'dark', key: 'settings.themeDark' },
]

function AccountSection() {
  const { t } = useTranslation()
  const session = useSession()
  const { signOut } = useAuth()
  const navigate = useNavigate()
  return (
    <section className="rounded-panel border border-line bg-surface p-4 tablet:p-6">
      <h2 className="text-h4">{t('account.title')}</h2>
      <p className="m-0 mt-4 text-small break-words">
        {t('account.signedInAs', { name: session.user.name, email: session.user.email })}
      </p>
      <dl className="m-0 mt-4 grid grid-cols-[auto_1fr] text-small">
        <dt className="flex min-h-11 items-center border-b border-line pr-6 text-ink-muted">
          {t('account.workspace')}
        </dt>
        <dd className="m-0 flex min-h-11 items-center border-b border-line">
          {session.organization.name}
        </dd>
        <dt className="flex min-h-11 items-center pr-6 text-ink-muted">{t('account.role')}</dt>
        <dd className="m-0 flex min-h-11 items-center">{t(`account.roles.${session.role}`)}</dd>
      </dl>
      <Button
        variant="secondary"
        className="mt-4"
        onClick={() => void signOut().then(() => navigate('/signin', { replace: true }))}
      >
        {t('account.signOut')}
      </Button>
    </section>
  )
}

export function SettingsPage() {
  const { t } = useTranslation()
  const { theme, setTheme } = useTheme()
  const canManage = useCan('org.manage')
  useEffect(() => {
    document.title = `${t('pages.settings.title')} · ${t('app.name')}`
  }, [t])

  return (
    <>
      <PageHeader title={t('pages.settings.title')} intro={t('pages.settings.intro')} />
      <div className="grid gap-6 desktop:grid-cols-2">
        <AccountSection />
        <WorkspaceSection />
        <SecuritySection />
        <DevicesSection />
        <AiPlanSection />
        <UsageSection />
        {canManage ? (
          <Suspense fallback={null}>
            <BillingSection />
          </Suspense>
        ) : null}
        <PrivacySection />
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
