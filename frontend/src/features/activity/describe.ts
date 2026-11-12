import type { TFunction } from 'i18next'
import type { AuditEntry } from '../../lib/teamApi'

/** i18n keys cannot contain dots (they mean nesting), so actions are looked up with underscores. */
export function asKey(action: string): string {
  return action.replaceAll('.', '_')
}

function roleWord(t: TFunction, role: unknown): string {
  return typeof role === 'string' ? t(`account.roles.${role}`, { defaultValue: role }) : ''
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

/** "Changed ravi@urbancart.in from Analyst to Viewer", "Sign-in failed (wrong password)", … */
export function describeEntry(t: TFunction, entry: AuditEntry): string {
  const details = entry.details
  // Connection entries name things (a file, a connection), not roles.
  const names = entry.action.startsWith('connection.') || entry.action.startsWith('dictionary.')
  const sentence = t(`activity.actions.${asKey(entry.action)}`, {
    defaultValue: entry.action,
    email: typeof details.email === 'string' ? details.email : '',
    role: roleWord(t, details.role),
    from: names ? text(details.from) : roleWord(t, details.from),
    to: names ? text(details.to) : roleWord(t, details.to),
    name: text(details.name),
    version: text(details.version),
    person: text(details.user_name),
    plan:
      typeof details.to === 'string' && !names
        ? t(`ai.plans.${details.to}`, { defaultValue: details.to })
        : '',
    provider:
      typeof details.provider === 'string'
        ? t(`ai.providers.${details.provider}`, { defaultValue: details.provider })
        : '',
    last4: typeof details.last4 === 'string' ? details.last4 : '',
  })
  const reason = details.reason
  if (entry.action === 'auth.login.failure' && typeof reason === 'string') {
    return `${sentence} (${t(`activity.failureReasons.${reason}`, { defaultValue: reason })})`
  }
  return sentence
}
