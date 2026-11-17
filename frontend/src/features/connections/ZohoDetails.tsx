import './strings'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/ui/button'
import { SelectField } from '../../components/ui/select'
import { connectionsApi, type ZohoBooks } from '../../lib/connectionsApi'
import { formatNumber } from '../../lib/format'

/** The organisation, data centre and today's Zoho API calls of a Zoho Books connection (M13). */
export function ZohoLines({ zoho }: { zoho: ZohoBooks }) {
  const { t } = useTranslation()
  return (
    <>
      <p className="m-0 mt-2 text-small text-ink-muted">
        {t('connections.zoho.organizationLine', {
          name: zoho.organization_name ?? zoho.organization_id,
        })}
        {zoho.currency
          ? ` · ${t('connections.zoho.currencyLine', { currency: zoho.currency })}`
          : null}
        {' · '}
        {t('connections.zoho.regionLine', { region: zoho.region_name })}
        {'. '}
        {t('connections.zoho.readOnlyNote')}
      </p>
      {typeof zoho.calls_today === 'number' && zoho.daily_limit ? (
        <p className="m-0 mt-2 text-small text-ink-muted tabular">
          {t('connections.zoho.callsLine', {
            used: formatNumber(zoho.calls_today),
            limit: formatNumber(zoho.daily_limit),
          })}{' '}
          {t('connections.zoho.callsNote')}
        </p>
      ) : null}
    </>
  )
}

/** Owners: read another organisation the signed-in Zoho account can see. */
export function ZohoOrganizationPicker({
  connectionId,
  zoho,
  disabled,
  onDone,
  onError,
}: {
  connectionId: string
  zoho: ZohoBooks
  disabled: boolean
  onDone: (text: string) => void
  onError: (error: unknown) => void
}) {
  const { t } = useTranslation()
  const [chosen, setChosen] = useState(zoho.organizations[0]?.id ?? '')
  const save = useMutation({
    mutationFn: () => connectionsApi.zohoOrganization(connectionId, chosen),
    onSuccess: (connection) =>
      onDone(
        t('connections.zoho.switched', { name: connection.zoho?.organization_name ?? chosen }),
      ),
    onError,
  })
  if (!zoho.organizations.length) return null
  return (
    <div className="mt-4 flex flex-wrap items-end gap-3">
      <SelectField
        label={t('connections.zoho.switchLabel')}
        value={chosen}
        onChange={(event) => setChosen(event.target.value)}
        aria-describedby="zoho-switch-hint"
        className="min-w-0 grow tablet:max-w-sm"
      >
        {zoho.organizations.map((org) => (
          <option key={org.id} value={org.id}>
            {org.name}
          </option>
        ))}
      </SelectField>
      <Button
        variant="secondary"
        disabled={disabled || save.isPending || !chosen}
        onClick={() => save.mutate()}
      >
        {save.isPending ? t('connections.zoho.switching') : t('connections.zoho.switchButton')}
      </Button>
      <p id="zoho-switch-hint" className="m-0 basis-full text-caption text-ink-muted">
        {t('connections.zoho.switchHint')}
      </p>
    </div>
  )
}
