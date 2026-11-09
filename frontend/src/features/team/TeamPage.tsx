import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MailPlus, Users } from 'lucide-react'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { Select, SelectField } from '../../components/ui/select'
import { ApiError } from '../../lib/api'
import { useSession } from '../../lib/authContext'
import { cn } from '../../lib/cn'
import { formatDate, formatRelativeTime } from '../../lib/format'
import type { Role } from '../../lib/role'
import { teamApi, type Invitation, type InviteRole, type Member } from '../../lib/teamApi'
import { looksLikeEmail } from '../auth/linkToken'

const MEMBERS = ['team', 'members'] as const
const INVITATIONS = ['team', 'invitations'] as const
const ROLES: Role[] = ['owner', 'analyst', 'viewer']

type Notice = { tone: 'success' | 'error'; text: string } | null

function Section({
  title,
  intro,
  children,
  className,
  id,
}: {
  title: string
  intro?: string
  children: ReactNode
  className?: string
  id: string
}) {
  return (
    <section
      aria-labelledby={id}
      className={cn('rounded-panel border border-line bg-surface p-4 tablet:p-6', className)}
    >
      <h2 id={id} className="text-h4">
        {title}
      </h2>
      {intro ? <p className="m-0 mt-2 max-w-prose text-small text-ink-muted">{intro}</p> : null}
      {children}
    </section>
  )
}

function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

// ------------------------------------------------------------------------------------------------ people
function MemberRow({
  member,
  onRole,
  onRemove,
  busy,
}: {
  member: Member
  onRole: (role: Role) => void
  onRemove: () => void
  busy: boolean
}) {
  const { t } = useTranslation()
  const { organization } = useSession()
  return (
    <li
      className={cn(
        'grid items-center gap-x-4 gap-y-2 border-b border-line py-3 last:border-b-0',
        // Phone: name, then role + Remove on one line, then two-step + joined. Tablet up: one ledger row.
        "grid-cols-[minmax(0,1fr)_auto] [grid-template-areas:'person_person'_'role_action'_'twostep_joined']",
        "tablet:min-h-11 tablet:grid-cols-[minmax(0,1fr)_160px_88px_112px_96px] tablet:py-2 tablet:[grid-template-areas:'person_role_twostep_joined_action']",
      )}
    >
      <div className="min-w-0 [grid-area:person]">
        <p className="m-0 truncate text-small font-medium text-ink">
          {member.name}
          {member.is_you ? (
            <span className="ml-2 rounded-pill bg-neel-soft px-2 text-caption text-neel">
              {t('team.you')}
            </span>
          ) : null}
        </p>
        <p className="m-0 truncate text-caption text-ink-muted">{member.email}</p>
      </div>
      <div className="[grid-area:role]">
        {member.is_you ? (
          <span className="text-small text-ink">{t(`account.roles.${member.role}`)}</span>
        ) : (
          <Select
            aria-label={t('team.roleFor', { name: member.name })}
            value={member.role}
            disabled={busy}
            onChange={(event) => onRole(event.target.value as Role)}
          >
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {t(`account.roles.${role}`)}
              </option>
            ))}
          </Select>
        )}
      </div>
      <p className="m-0 text-caption text-ink-muted [grid-area:twostep] tablet:text-small">
        <span className="tablet:sr-only">{t('team.columns.twoStep')}: </span>
        <span className={member.two_factor_enabled ? 'text-gain' : 'text-ink-muted'}>
          {member.two_factor_enabled ? t('team.twoStepOn') : t('team.twoStepOff')}
        </span>
      </p>
      <p className="m-0 text-caption text-ink-muted tabular-nums [grid-area:joined] tablet:text-small">
        <span className="tablet:sr-only">{t('team.columns.joined')}: </span>
        {formatDate(member.joined_at, organization.timezone)}
      </p>
      <div className="justify-self-end [grid-area:action]">
        {!member.is_you ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            aria-label={t('team.removeLabel', { name: member.name })}
            onClick={onRemove}
          >
            {t('team.remove')}
          </Button>
        ) : null}
      </div>
    </li>
  )
}

function People() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const { organization } = useSession()
  const members = useQuery({ queryKey: MEMBERS, queryFn: teamApi.members })
  const [notice, setNotice] = useState<Notice>(null)
  const [removing, setRemoving] = useState<Member | null>(null)

  const changeRole = useMutation({
    mutationFn: ({ member, role }: { member: Member; role: Role }) =>
      teamApi.changeRole(member.id, role),
    onSuccess: (updated) => {
      const role = t(`account.roles.${updated.role}`)
      const text =
        updated.role === 'owner' && !updated.two_factor_enabled
          ? `${t('team.roleChanged', { name: updated.name, role })} ${t('team.ownerNeeds2fa', { name: updated.name })}`
          : t('team.roleChanged', { name: updated.name, role })
      setNotice({ tone: 'success', text })
      void client.invalidateQueries({ queryKey: MEMBERS })
    },
    onError: (error) => {
      setNotice({ tone: 'error', text: message(error, t('team.loadFailed')) })
      void client.invalidateQueries({ queryKey: MEMBERS })
    },
  })
  const remove = useMutation({
    mutationFn: (member: Member) => teamApi.removeMember(member.id),
    onSuccess: (_, member) => {
      setRemoving(null)
      setNotice({ tone: 'success', text: t('team.removed', { name: member.name }) })
      void client.invalidateQueries({ queryKey: MEMBERS })
    },
    onError: (error) => {
      setRemoving(null)
      setNotice({ tone: 'error', text: message(error, t('team.loadFailed')) })
    },
  })
  const busy = changeRole.isPending || remove.isPending

  return (
    <Section
      id="people-title"
      className="desktop:col-span-2"
      title={t('team.membersTitle')}
      intro={t('team.membersIntro', { org: organization.name })}
    >
      {notice ? (
        <FormAlert tone={notice.tone} className="mt-4">
          {notice.text}
        </FormAlert>
      ) : null}
      {members.isPending ? (
        <p role="status" className="m-0 mt-4 text-small text-ink-muted">
          {t('team.loading')}
        </p>
      ) : null}
      {members.isError ? (
        <FormAlert tone="error" className="mt-4">
          {t('team.loadFailed')}
        </FormAlert>
      ) : null}
      {members.data ? (
        <>
          <div
            aria-hidden
            className="mt-4 hidden grid-cols-[minmax(0,1fr)_160px_88px_112px_96px] gap-x-4 border-b border-line pb-2 text-caption font-medium text-ink-muted tablet:grid"
          >
            <span>{t('team.columns.person')}</span>
            <span>{t('team.columns.role')}</span>
            <span>{t('team.columns.twoStep')}</span>
            <span>{t('team.columns.joined')}</span>
            <span />
          </div>
          <ul className="m-0 list-none p-0" aria-label={t('team.membersTitle')}>
            {members.data.members.map((member) => (
              <MemberRow
                key={member.id}
                member={member}
                busy={busy}
                onRole={(role) => changeRole.mutate({ member, role })}
                onRemove={() => setRemoving(member)}
              />
            ))}
          </ul>
        </>
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t('team.removeTitle', { name: removing?.name ?? '' })}
        description={t('team.removeBody', { name: removing?.name ?? '', org: organization.name })}
        confirmLabel={t('team.removeConfirm', { name: removing?.name ?? '' })}
        busy={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing)}
      />
    </Section>
  )
}

// ------------------------------------------------------------------------------------------- invitations
function InviteForm({ onSent }: { onSent: (email: string) => void }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<InviteRole>('analyst')
  const [error, setError] = useState<string | undefined>()
  const [problem, setProblem] = useState<string | null>(null)
  const invite = useMutation({
    mutationFn: () => teamApi.invite(email.trim(), role),
    onSuccess: (created) => {
      setEmail('')
      onSent(created.email)
      void client.invalidateQueries({ queryKey: INVITATIONS })
    },
    onError: (caught) => {
      if (caught instanceof ApiError && caught.fields.email) setError(caught.fields.email)
      else setProblem(message(caught, t('team.loadFailed')))
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    if (!looksLikeEmail(email)) {
      setError(t('auth.invalidEmail'))
      return
    }
    setError(undefined)
    invite.mutate()
  }
  return (
    <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
      {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
      <Field
        label={t('team.email')}
        type="email"
        name="email"
        autoComplete="off"
        inputMode="email"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        error={error}
      />
      <div>
        <SelectField
          label={t('team.role')}
          name="role"
          value={role}
          onChange={(event) => setRole(event.target.value as InviteRole)}
          aria-describedby="invite-role-help"
        >
          <option value="analyst">{t('account.roles.analyst')}</option>
          <option value="viewer">{t('account.roles.viewer')}</option>
        </SelectField>
        <p id="invite-role-help" className="m-0 mt-1.5 text-small text-ink-muted">
          {t(`team.roleHelp.${role}`)}
        </p>
      </div>
      <Button type="submit" disabled={invite.isPending} className="w-fit">
        <MailPlus aria-hidden />
        {t('team.sendInvite')}
      </Button>
    </form>
  )
}

function InvitationRow({
  invitation,
  onResend,
  onCancel,
  busy,
}: {
  invitation: Invitation
  onResend: () => void
  onCancel: () => void
  busy: boolean
}) {
  const { t } = useTranslation()
  const { organization } = useSession()
  const when = formatRelativeTime(invitation.created_at)
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="m-0 truncate text-small font-medium text-ink">
          {invitation.email}
          <span className="ml-2 text-caption font-normal text-ink-muted">
            {t(`account.roles.${invitation.role}`)}
          </span>
        </p>
        <p className="m-0 text-caption text-ink-muted tabular-nums">
          {invitation.invited_by_name
            ? t('team.invitedBy', { name: invitation.invited_by_name, when })
            : t('team.invitedWhen', { when })}
          {' · '}
          {invitation.expired ? (
            <span className="text-caution">{t('team.expired')}</span>
          ) : (
            t('team.expiresOn', { date: formatDate(invitation.expires_at, organization.timezone) })
          )}
        </p>
      </div>
      <div className="flex gap-1">
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          aria-label={t('team.resendLabel', { email: invitation.email })}
          onClick={onResend}
        >
          {t('team.resend')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          aria-label={t('team.cancelInviteLabel', { email: invitation.email })}
          onClick={onCancel}
        >
          {t('team.cancelInvite')}
        </Button>
      </div>
    </li>
  )
}

function Invitations() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const invitations = useQuery({ queryKey: INVITATIONS, queryFn: teamApi.invitations })
  const [notice, setNotice] = useState<Notice>(null)
  const [cancelling, setCancelling] = useState<Invitation | null>(null)
  const refresh = () => client.invalidateQueries({ queryKey: INVITATIONS })
  const resend = useMutation({
    mutationFn: (invitation: Invitation) => teamApi.resendInvitation(invitation.id),
    onSuccess: (updated) => {
      setNotice({ tone: 'success', text: t('team.resent', { email: updated.email }) })
      void refresh()
    },
    onError: (error) => setNotice({ tone: 'error', text: message(error, t('team.loadFailed')) }),
  })
  const cancel = useMutation({
    mutationFn: (invitation: Invitation) => teamApi.revokeInvitation(invitation.id),
    onSuccess: (_, invitation) => {
      setCancelling(null)
      setNotice({ tone: 'success', text: t('team.cancelled', { email: invitation.email }) })
      void refresh()
    },
    onError: (error) => {
      setCancelling(null)
      setNotice({ tone: 'error', text: message(error, t('team.loadFailed')) })
    },
  })
  const items = invitations.data?.invitations ?? []
  return (
    <>
      <Section id="invite-title" title={t('team.inviteTitle')} intro={t('team.inviteIntro')}>
        <InviteForm
          onSent={(email) => setNotice({ tone: 'success', text: t('team.inviteSent', { email }) })}
        />
      </Section>
      <Section id="pending-title" title={t('team.pendingTitle')}>
        {notice ? (
          <FormAlert tone={notice.tone} className="mt-4">
            {notice.text}
          </FormAlert>
        ) : null}
        {invitations.isError ? (
          <FormAlert tone="error" className="mt-4">
            {t('team.loadFailed')}
          </FormAlert>
        ) : null}
        {invitations.isSuccess && !items.length ? (
          <p className="m-0 mt-4 text-small text-ink-muted">{t('team.pendingEmpty')}</p>
        ) : null}
        {items.length ? (
          <ul className="m-0 mt-2 list-none p-0" aria-label={t('team.pendingTitle')}>
            {items.map((invitation) => (
              <InvitationRow
                key={invitation.id}
                invitation={invitation}
                busy={resend.isPending || cancel.isPending}
                onResend={() => resend.mutate(invitation)}
                onCancel={() => setCancelling(invitation)}
              />
            ))}
          </ul>
        ) : null}
        <ConfirmDialog
          open={cancelling !== null}
          onOpenChange={(open) => !open && setCancelling(null)}
          title={t('team.cancelTitle', { email: cancelling?.email ?? '' })}
          description={t('team.cancelBody')}
          confirmLabel={t('team.cancelConfirm')}
          busy={cancel.isPending}
          onConfirm={() => cancelling && cancel.mutate(cancelling)}
        />
      </Section>
    </>
  )
}

/** Team (BUILD_SPEC 11): members, roles, invite, remove. Owners only; the route is hidden for others. */
export function TeamPage() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const allowed = permissions.includes('members.manage')
  useEffect(() => {
    document.title = `${t('pages.team.title')} · ${t('app.name')}`
  }, [t])
  return (
    <>
      <PageHeader title={t('pages.team.title')} intro={t('pages.team.intro')} />
      {allowed ? (
        <div className="grid gap-6 desktop:grid-cols-2">
          <People />
          <Invitations />
        </div>
      ) : (
        <EmptyState className="max-w-2xl" icon={Users} message={t('team.ownersOnly')} />
      )}
    </>
  )
}
