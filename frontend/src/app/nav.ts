import {
  Activity,
  CalendarCheck,
  Gauge,
  LayoutGrid,
  ListChecks,
  Mail,
  MessageSquareText,
  Plug,
  Settings,
  SlidersHorizontal,
  Target,
  Users,
  type LucideIcon,
} from 'lucide-react'
import type { Role } from '../lib/role'

export interface NavItem {
  path: string
  /** i18n key under nav.* and pages.* */
  key:
    | 'today'
    | 'ask'
    | 'dashboard'
    | 'whatIf'
    | 'goals'
    | 'briefings'
    | 'tasks'
    | 'connections'
    | 'team'
    | 'activity'
    | 'settings'
  icon: LucideIcon
  ownerOnly?: boolean
}

// Order follows BUILD_SPEC 11a.4.
export const mainNav: NavItem[] = [
  { path: '/today', key: 'today', icon: CalendarCheck },
  { path: '/ask', key: 'ask', icon: MessageSquareText },
  { path: '/dashboard', key: 'dashboard', icon: LayoutGrid },
  { path: '/what-if', key: 'whatIf', icon: SlidersHorizontal },
  { path: '/goals', key: 'goals', icon: Target },
  { path: '/briefings', key: 'briefings', icon: Mail },
  { path: '/tasks', key: 'tasks', icon: ListChecks },
]

export const adminNav: NavItem[] = [
  { path: '/connections', key: 'connections', icon: Plug, ownerOnly: true },
  { path: '/team', key: 'team', icon: Users, ownerOnly: true },
  { path: '/activity', key: 'activity', icon: Activity, ownerOnly: true },
  { path: '/settings', key: 'settings', icon: Settings, ownerOnly: true },
]

/** Phone bottom tabs: Today, Ask, Dashboard, then "More" for everything else. */
export const mobileTabs: NavItem['key'][] = ['today', 'ask', 'dashboard']

export function visibleAdminNav(role: Role): NavItem[] {
  return adminNav.filter((item) => !item.ownerOnly || role === 'owner')
}

export const brandIcon = Gauge
