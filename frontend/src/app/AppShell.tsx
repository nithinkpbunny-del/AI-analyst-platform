import { LogOut, Menu, MessageSquareText, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { lazy, Suspense, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { WorkspaceSwitcher } from '../components/WorkspaceSwitcher'
import { Sheet, SheetContent, SheetTrigger } from '../components/ui/sheet'
import { useAuth, useSession } from '../lib/authContext'
import { cn } from '../lib/cn'
import { brandIcon as BrandIcon, mainNav, mobileTabs, visibleAdminNav, type NavItem } from './nav'

const COLLAPSE_KEY = 'analyst.sidebarCollapsed'

function useSignOut() {
  const { signOut } = useAuth()
  const navigate = useNavigate()
  return async () => {
    await signOut()
    void navigate('/signin', { replace: true })
  }
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

function SideLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const { t } = useTranslation()
  const label = t(`nav.${item.key}`)
  const Icon = item.icon
  return (
    <NavLink
      to={item.path}
      title={label}
      className={({ isActive }) =>
        cn(
          'flex min-h-11 items-center gap-3 rounded-control px-3 text-small font-medium',
          'justify-center desktop:justify-start',
          collapsed && 'desktop:justify-center',
          isActive ? 'bg-neel-soft text-neel' : 'text-ink-muted hover:bg-neel-soft hover:text-ink',
        )
      }
    >
      <Icon className="size-5 shrink-0" aria-hidden strokeWidth={1.5} />
      <span className={cn('sr-only', !collapsed && 'desktop:not-sr-only')}>{label}</span>
    </NavLink>
  )
}

/** Desktop: collapsible sidebar. Tablet: icon rail. Hidden on phones (bottom tabs instead). */
function SideNav() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const signOut = useSignOut()
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = () => {
    setCollapsed((value) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, value ? '0' : '1')
      } catch {
        /* ignore */
      }
      return !value
    })
  }
  const admin = visibleAdminNav(permissions)

  return (
    <nav
      aria-label={t('nav.main')}
      data-testid="side-nav"
      data-collapsed={collapsed}
      className={cn(
        'sticky top-0 hidden h-dvh shrink-0 flex-col gap-1 overflow-y-auto border-r border-line bg-surface p-3 tablet:flex',
        'w-[72px]',
        collapsed ? 'desktop:w-[72px]' : 'desktop:w-60',
      )}
    >
      <div className="mb-4 flex min-h-11 items-center gap-2 px-2 text-ink">
        <BrandIcon className="size-6 shrink-0 text-neel" aria-hidden strokeWidth={1.75} />
        <span
          className={cn(
            'sr-only font-display text-h4 font-semibold',
            !collapsed && 'desktop:not-sr-only',
          )}
        >
          {t('app.name')}
        </span>
      </div>
      {/* Tablet rail: the initial only. Desktop: the name, unless the sidebar is collapsed. */}
      <WorkspaceSwitcher compact className="desktop:hidden" />
      {collapsed ? (
        <WorkspaceSwitcher compact className="hidden desktop:flex" />
      ) : (
        <WorkspaceSwitcher className="hidden desktop:flex" />
      )}
      <hr className="mt-2 mb-3 border-line" />
      {mainNav.map((item) => (
        <SideLink key={item.key} item={item} collapsed={collapsed} />
      ))}
      {admin.length > 0 && (
        <>
          <hr className="my-3 border-line" />
          {admin.map((item) => (
            <SideLink key={item.key} item={item} collapsed={collapsed} />
          ))}
        </>
      )}
      <button
        type="button"
        onClick={() => void signOut()}
        title={t('account.signOut')}
        className={cn(
          'mt-auto flex min-h-11 items-center gap-3 rounded-control px-3 text-small font-medium text-ink-muted hover:bg-neel-soft hover:text-ink',
          'justify-center desktop:justify-start',
          collapsed && 'desktop:justify-center',
        )}
      >
        <LogOut className="size-5 shrink-0" aria-hidden strokeWidth={1.5} />
        <span className={cn('sr-only', !collapsed && 'desktop:not-sr-only')}>
          {t('account.signOut')}
        </span>
      </button>
      <button
        type="button"
        onClick={toggle}
        aria-label={collapsed ? t('nav.expand') : t('nav.collapse')}
        aria-pressed={collapsed}
        className="hidden min-h-11 items-center justify-center rounded-control text-ink-muted hover:bg-neel-soft hover:text-ink desktop:flex"
      >
        {collapsed ? (
          <PanelLeftOpen className="size-5" aria-hidden strokeWidth={1.5} />
        ) : (
          <PanelLeftClose className="size-5" aria-hidden strokeWidth={1.5} />
        )}
      </button>
    </nav>
  )
}

/** Phones: bottom tab bar (Today, Ask, Dashboard, More) and a floating Ask button. */
function MobileNav() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const location = useLocation()
  const signOut = useSignOut()
  const [moreOpen, setMoreOpen] = useState(false)
  const tabs = mainNav.filter((item) => mobileTabs.includes(item.key))
  const rest = [
    ...mainNav.filter((item) => !mobileTabs.includes(item.key)),
    ...visibleAdminNav(permissions),
  ]
  const moreActive = rest.some((item) => location.pathname.startsWith(item.path))
  const tabClass = (active: boolean) =>
    cn(
      'flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-caption font-medium',
      active ? 'text-neel' : 'text-ink-muted',
    )

  return (
    <>
      {!location.pathname.startsWith('/ask') && (
        <NavLink
          to="/ask"
          aria-label={t('nav.askFab')}
          data-testid="ask-fab"
          className="fixed right-4 bottom-[calc(72px+env(safe-area-inset-bottom))] z-30 flex min-h-12 items-center gap-2 rounded-pill bg-neel px-5 text-small font-semibold text-on-neel shadow-overlay tablet:hidden"
        >
          <MessageSquareText className="size-5" aria-hidden strokeWidth={1.75} />
          {t('nav.ask')}
        </NavLink>
      )}
      <nav
        aria-label={t('nav.main')}
        data-testid="bottom-tabs"
        className="fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] tablet:hidden"
      >
        {tabs.map((item) => {
          const Icon = item.icon
          return (
            <NavLink key={item.key} to={item.path} className={({ isActive }) => tabClass(isActive)}>
              <Icon className="size-5" aria-hidden strokeWidth={1.5} />
              {t(`nav.${item.key}`)}
            </NavLink>
          )
        })}
        <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
          <SheetTrigger className={tabClass(moreActive)}>
            <Menu className="size-5" aria-hidden strokeWidth={1.5} />
            {t('nav.more')}
          </SheetTrigger>
          <SheetContent title={t('nav.more')}>
            <ul className="m-0 grid list-none grid-cols-2 gap-2 p-0">
              {rest.map((item) => {
                const Icon = item.icon
                return (
                  <li key={item.key}>
                    <NavLink
                      to={item.path}
                      onClick={() => setMoreOpen(false)}
                      className={({ isActive }) =>
                        cn(
                          'flex min-h-12 items-center gap-3 rounded-control border border-line px-3 text-small font-medium',
                          isActive ? 'bg-neel-soft text-neel' : 'text-ink',
                        )
                      }
                    >
                      <Icon className="size-5" aria-hidden strokeWidth={1.5} />
                      {t(`nav.${item.key}`)}
                    </NavLink>
                  </li>
                )
              })}
            </ul>
            <button
              type="button"
              onClick={() => {
                setMoreOpen(false)
                void signOut()
              }}
              className="mt-4 flex min-h-12 w-full items-center gap-3 rounded-control border border-line px-3 text-small font-medium text-ink"
            >
              <LogOut className="size-5" aria-hidden strokeWidth={1.5} />
              {t('account.signOut')}
            </button>
          </SheetContent>
        </Sheet>
      </nav>
    </>
  )
}

// The command palette loads the first time someone presses Ctrl/Cmd + K.
const CommandPalette = lazy(() =>
  import('../features/palette/CommandPalette').then((m) => ({ default: m.CommandPalette })),
)

function usePaletteShortcut(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return [open, setOpen]
}

export function AppShell() {
  const { t } = useTranslation()
  const [paletteOpen, setPaletteOpen] = usePaletteShortcut()
  return (
    <div className="flex min-h-dvh bg-paper text-ink">
      <a
        href="#main"
        className="sr-only z-50 rounded-control bg-neel px-4 py-2 text-on-neel focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        {t('app.skipToContent')}
      </a>
      <SideNav />
      <div className="min-w-0 flex-1">
        <header className="flex min-h-14 items-center gap-2 border-b border-line bg-surface px-4 tablet:hidden">
          <BrandIcon className="size-5 shrink-0 text-neel" aria-hidden strokeWidth={1.75} />
          <span className="font-display text-h4 font-semibold">{t('app.name')}</span>
          <WorkspaceSwitcher className="ml-auto w-auto max-w-[60%]" />
        </header>
        <main
          id="main"
          tabIndex={-1}
          className="mx-0 w-full max-w-[1120px] px-4 pt-6 pb-36 tablet:px-8 tablet:pt-8 tablet:pb-12"
        >
          <Outlet />
        </main>
      </div>
      <MobileNav />
      {paletteOpen ? (
        <Suspense fallback={null}>
          <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
        </Suspense>
      ) : null}
    </div>
  )
}
