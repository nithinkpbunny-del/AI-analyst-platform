import './strings'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { SelectField } from '../../components/ui/select'
import { ApiError } from '../../lib/api'
import {
  connectionsApi,
  type Connection,
  type DatabaseEngine,
  type Sources,
  type SslMode,
} from '../../lib/connectionsApi'
import { CONNECTIONS } from './keys'

const DEFAULT_PORTS: Record<DatabaseEngine, number> = { postgresql: 5432, mysql: 3306 }
type FieldName = 'host' | 'port' | 'database' | 'username' | 'password'

/**
 * Connect a PostgreSQL or MySQL database, or replace the credentials of one (M14). The server checks the address
 * (public only, outside development), connects read-only, lists the tables and says whether the user could write;
 * the password is stored encrypted and never shown again. Loaded on demand.
 */
export function DatabaseDialog({
  open,
  onOpenChange,
  sources,
  connection,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  sources?: Sources
  /** Set when replacing the credentials of this connection. */
  connection?: Connection
  onDone?: (message: string) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const navigate = useNavigate()
  const current = connection?.database
  const replacing = connection !== undefined
  const [engine, setEngine] = useState<DatabaseEngine>(current?.engine ?? 'postgresql')
  const [host, setHost] = useState(current?.host ?? '')
  const [port, setPort] = useState(current ? String(current.port) : '')
  const [database, setDatabase] = useState(current?.database ?? '')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [ssl, setSsl] = useState<SslMode>(current?.ssl_mode ?? 'require')
  const [name, setName] = useState('')
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const sslOffAllowed = sources?.database?.ssl_off_allowed ?? current?.ssl_mode === 'disable'
  const demoHosts = sources?.database?.demo_hosts ?? []

  const save = useMutation({
    mutationFn: () => {
      const body = {
        engine,
        host: host.trim(),
        ...(port.trim() ? { port: Number(port.trim()) } : {}),
        database: database.trim(),
        username: username.trim(),
        password,
        ssl_mode: ssl,
        ...(name.trim() && !replacing ? { name: name.trim() } : {}),
      }
      return connection
        ? connectionsApi.replaceDatabaseCredentials(connection.id, body)
        : connectionsApi.addDatabase(body)
    },
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: CONNECTIONS })
      setPassword('')
      onOpenChange(false)
      if (replacing) onDone?.(t('connections.database.replaced'))
      else void navigate(`/connections/${saved.id}`, { state: { databaseAdded: true } })
    },
    onError: (error) =>
      setProblem(error instanceof ApiError ? error.message : t('connections.database.failed')),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const required = t('connections.database.required')
    const portNumber = Number(port.trim())
    const next: Partial<Record<FieldName, string>> = {
      host: host.trim() ? undefined : required,
      database: database.trim() ? undefined : required,
      username: username.trim() ? undefined : required,
      password: password ? undefined : required,
      port:
        !port.trim() || (Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535)
          ? undefined
          : t('connections.database.portInvalid'),
    }
    setErrors(next)
    setProblem(null)
    if (!Object.values(next).some(Boolean)) save.mutate()
  }

  const modes: SslMode[] = sslOffAllowed
    ? ['verify-full', 'require', 'disable']
    : ['verify-full', 'require']

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={t(replacing ? 'connections.database.replaceTitle' : 'connections.database.title')}
        description={t('connections.database.intro')}
      >
        <form onSubmit={submit} className="mt-4 flex flex-col gap-4" noValidate>
          {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
          {demoHosts.length && !replacing ? (
            <p className="m-0 text-caption text-ink-muted">
              {t('connections.database.demoHint', { hosts: demoHosts.join(' or ') })}
            </p>
          ) : null}
          <SelectField
            label={t('connections.database.engine')}
            value={engine}
            disabled={replacing}
            onChange={(event) => setEngine(event.target.value as DatabaseEngine)}
          >
            <option value="postgresql">{t('connections.database.engines.postgresql')}</option>
            <option value="mysql">{t('connections.database.engines.mysql')}</option>
          </SelectField>
          <div className="grid grid-cols-1 gap-4 tablet:grid-cols-[1fr_8rem]">
            <Field
              label={t('connections.database.host')}
              hint={t('connections.database.hostHint')}
              error={errors.host}
              value={host}
              onChange={(event) => setHost(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              inputMode="url"
              maxLength={253}
            />
            <Field
              label={t('connections.database.port')}
              hint={t('connections.database.portHint', { port: DEFAULT_PORTS[engine] })}
              error={errors.port}
              value={port}
              onChange={(event) => setPort(event.target.value)}
              inputMode="numeric"
              autoComplete="off"
              placeholder={String(DEFAULT_PORTS[engine])}
            />
          </div>
          <Field
            label={t('connections.database.database')}
            error={errors.database}
            value={database}
            onChange={(event) => setDatabase(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            maxLength={128}
          />
          <Field
            label={t('connections.database.username')}
            error={errors.username}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            maxLength={128}
          />
          <Field
            label={t('connections.database.password')}
            hint={t('connections.database.passwordHint')}
            error={errors.password}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            revealable
            autoComplete="new-password"
            spellCheck={false}
          />
          <SelectField
            label={t('connections.database.ssl')}
            value={ssl}
            onChange={(event) => setSsl(event.target.value as SslMode)}
            aria-describedby="database-ssl-hint"
          >
            {modes.map((mode) => (
              <option key={mode} value={mode}>
                {t(`connections.database.sslModes.${mode}`)}
              </option>
            ))}
          </SelectField>
          <p id="database-ssl-hint" className="-mt-2 mb-0 text-caption text-ink-muted">
            {t('connections.database.sslHint')}
          </p>
          {replacing ? null : (
            <Field
              label={t('connections.database.name')}
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={120}
            />
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending
                ? t('connections.database.checking')
                : t(replacing ? 'connections.database.replace' : 'connections.database.check')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
