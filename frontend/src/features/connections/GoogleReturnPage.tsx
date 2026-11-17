import './strings'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { ApiError } from '../../lib/api'
import { connectionsApi } from '../../lib/connectionsApi'
import { CONNECTIONS } from './keys'
import { useSpreadsheetPicker } from './useSpreadsheetPicker'

type Step = 'finishing' | 'picking' | 'saving'

/**
 * Google sends the browser back to the API, which forwards it here with `#state=…&code=…` (or `#error=…`).
 * The signed-in Owner finishes the sign-in, chooses spreadsheets with the Picker, and lands on the connection.
 * The fragment is removed from the address at once, so the code is not left in the browser history.
 */
export function GoogleReturnPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const picker = useSpreadsheetPicker()
  // Read the fragment once: what Google said, before the address is cleaned.
  const [params] = useState(() => new URLSearchParams(location.hash.replace(/^#/, '')))
  const problem = params.get('error')
  const state = params.get('state')
  const code = params.get('code')
  const [step, setStep] = useState<Step>('finishing')
  const [error, setError] = useState<string | null>(() =>
    problem || !state || !code
      ? t(`connections.google.errors.${problem ?? 'google_failed'}`, {
          defaultValue: t('connections.google.errors.google_failed'),
        })
      : null,
  )
  const started = useRef(false)
  const pick = picker.pick

  useEffect(() => {
    if (started.current) return
    started.current = true
    void navigate(location.pathname, { replace: true })
    if (!state || !code || problem) return
    void (async () => {
      try {
        const finished = await connectionsApi.googleFinish(state, code)
        const connectionId = finished.connection.id
        void client.invalidateQueries({ queryKey: CONNECTIONS })
        if (finished.reconnected) {
          void navigate(`/connections/${connectionId}`, { replace: true })
          return
        }
        setStep('picking')
        const picked = await pick(finished.picker)
        if (picked.length) {
          setStep('saving')
          await connectionsApi.setSpreadsheets(
            connectionId,
            picked.map((sheet) => sheet.id),
          )
        }
        void client.invalidateQueries({ queryKey: CONNECTIONS })
        void navigate(`/connections/${connectionId}`, { replace: true })
      } catch (caught) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : t('connections.google.errors.google_failed'),
        )
      }
    })()
  }, [client, code, location.pathname, navigate, pick, problem, state, t])

  useEffect(() => {
    document.title = `${t('connections.google.title')} · ${t('app.name')}`
  }, [t])

  const status = {
    finishing: t('connections.google.working'),
    picking: t('connections.detail.chooseSpreadsheets'),
    saving: t('connections.google.saving'),
  }[step]

  return (
    <>
      <PageHeader title={t('connections.google.title')} />
      {error ? (
        <div className="flex max-w-2xl flex-col items-start gap-4">
          <FormAlert tone="error">{error}</FormAlert>
          <Button asChild variant="secondary">
            <Link to="/connections">{t('connections.google.startAgain')}</Link>
          </Button>
        </div>
      ) : (
        <p role="status" className="m-0 text-body text-ink">
          {status}
        </p>
      )}
      {picker.element}
    </>
  )
}
