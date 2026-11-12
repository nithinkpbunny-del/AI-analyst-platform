import {
  Activity,
  BookOpen,
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
import type { Permission } from '../lib/permissions'

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
    | 'dictionary'
    | 'team'
    | 'activity'
    | 'settings'
  icon: LucideIcon
  /** Shown only to people with this permission (BUILD_SPEC 6.2). */
  permission?: Permission
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
  // Analysts see the connections they may ask about, read-only (DECISIONS D49).
  { path: '/connections', key: 'connections', icon: Plug, permission: 'connections.view' },
  { path: '/dictionary', key: 'dictionary', icon: BookOpen, permission: 'connections.view' },
  { path: '/team', key: 'team', icon: Users, permission: 'members.manage' },
  // Analysts see their own entries (BUILD_SPEC 6.2), so Activity is not Owner-only.
  { path: '/activity', key: 'activity', icon: Activity, permission: 'audit.view_own' },
  // Everyone has their own account and security settings.
  { path: '/settings', key: 'settings', icon: Settings },
]

/** Phone bottom tabs: Today, Ask, Dashboard, then "More" for everything else. */
export const mobileTabs: NavItem['key'][] = ['today', 'ask', 'dashboard']

export function visibleAdminNav(permissions: readonly string[]): NavItem[] {
  return adminNav.filter((item) => !item.permission || permissions.includes(item.permission))
}

export const brandIcon = Gauge
