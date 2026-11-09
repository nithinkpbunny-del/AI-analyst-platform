import { useEffect, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Outlet } from 'react-router'
import { brandIcon as BrandIcon } from '../../app/nav'
import { Highlight } from '../../components/Highlight'

/**
 * Frame for the signed-out screens. Phone and tablet: one left-aligned column. Desktop: the form on the left and,
 * on the right, a real answer from the demo shop with the one haldi mark of the view (BUILD_SPEC 11a.1).
 */
export function AuthLayout() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-dvh bg-paper text-ink">
      <a
        href="#main"
        className="sr-only z-50 rounded-control bg-neel px-4 py-2 text-on-neel focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        {t('app.skipToContent')}
      </a>
      <div className="flex min-w-0 flex-1 flex-col px-4 py-6 tablet:px-12 tablet:py-10">
        <Link
          to="/signin"
          className="flex min-h-11 w-fit items-center gap-2 text-ink no-underline"
          aria-label={t('app.name')}
        >
          <BrandIcon className="size-6 text-neel" aria-hidden strokeWidth={1.75} />
          <span className="font-display text-h4 font-semibold">{t('app.name')}</span>
        </Link>
        <main id="main" tabIndex={-1} className="mt-10 w-full max-w-[440px] tablet:mt-16">
          <Outlet />
        </main>
      </div>
      <aside
        aria-label={t('auth.side.title')}
        className="hidden w-[min(44vw,600px)] shrink-0 flex-col justify-center border-l border-line bg-surface px-12 desktop:flex"
      >
        <p className="m-0 max-w-prose font-display text-h2 font-semibold">{t('auth.side.title')}</p>
        <div className="mt-10 max-w-prose rounded-sheet border border-line bg-paper p-6">
          <p className="m-0 text-small text-ink-muted">{t('auth.side.question')}</p>
          <p className="m-0 mt-3 font-display text-h4 font-semibold">
            {t('auth.side.leadBefore')}{' '}
            <Highlight sweep={false}>{t('auth.side.leadNumber')}</Highlight>{' '}
            {t('auth.side.leadAfter')}
          </p>
          <p className="m-0 mt-3 text-caption text-ink-muted">{t('auth.side.caption')}</p>
        </div>
        <ul className="m-0 mt-10 max-w-prose list-none p-0">
          {(['point1', 'point2', 'point3'] as const).map((key) => (
            <li key={key} className="flex min-h-11 items-center border-b border-line text-body">
              {t(`auth.side.${key}`)}
            </li>
          ))}
        </ul>
      </aside>
    </div>
  )
}

/** Title, intro and body of one signed-out screen. Sets the document title too. */
export function AuthPage({
  title,
  intro,
  children,
}: {
  title: string
  intro?: ReactNode
  children?: ReactNode
}) {
  const { t } = useTranslation()
  useEffect(() => {
    document.title = `${title} · ${t('app.name')}`
  }, [title, t])
  return (
    <>
      <h1 className="text-h2">{title}</h1>
      {intro ? <p className="m-0 mt-2 text-body text-ink-muted">{intro}</p> : null}
      <div className="mt-8 flex flex-col gap-6">{children}</div>
    </>
  )
}
