import type { TFunction } from 'i18next'
import type { AuditEntry } from '../../lib/teamApi'

/** i18n keys cannot contain dots (they mean nesting), so actions are looked up with underscores. */
export function asKey(action: string): string {
  return action.replaceAll('.', '_')
}

function roleWord(t: TFunction, role: unknown): string {
  return typeof role === 'string' ? t(`account.roles.${role}`, { defaultValue: role }) : ''
}

/** "Changed ravi@urbancart.in from Analyst to Viewer", "Sign-in failed (wrong password)", … */
export function describeEntry(t: TFunction, entry: AuditEntry): string {
  const details = entry.details
  const text = t(`activity.actions.${asKey(entry.action)}`, {
    defaultValue: entry.action,
    email: typeof details.email === 'string' ? details.email : '',
    role: roleWord(t, details.role),
    from: roleWord(t, details.from),
    to: roleWord(t, details.to),
  })
  const reason = details.reason
  if (entry.action === 'auth.login.failure' && typeof reason === 'string') {
    return `${text} (${t(`activity.failureReasons.${reason}`, { defaultValue: reason })})`
  }
  return text
}
