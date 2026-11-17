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

/**
 * Shopify sends the browser back to the API, which checks Shopify's signature and forwards it here with
 * `#state=…&code=…` (or `#error=…`). The signed-in Owner finishes the connection and lands on it. The fragment is
 * removed from the address at once, so the code is not left in the browser history. Loaded on demand.
 */
export function ShopifyReturnPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [params] = useState(() => new URLSearchParams(location.hash.replace(/^#/, '')))
  const problem = params.get('error')
  const state = params.get('state')
  const code = params.get('code')
  const [error, setError] = useState<string | null>(() =>
    problem || !state || !code
      ? t(`connections.shopify.errors.${problem ?? 'shopify_failed'}`, {
          defaultValue: t('connections.shopify.errors.shopify_failed'),
        })
      : null,
  )
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    void navigate(location.pathname, { replace: true })
    if (!state || !code || problem) return
    void (async () => {
      try {
        const finished = await connectionsApi.shopifyFinish(state, code)
        void client.invalidateQueries({ queryKey: CONNECTIONS })
        void navigate(`/connections/${finished.connection.id}`, { replace: true })
      } catch (caught) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : t('connections.shopify.errors.shopify_failed'),
        )
      }
    })()
  }, [client, code, location.pathname, navigate, problem, state, t])

  useEffect(() => {
    document.title = `${t('connections.shopify.returnTitle')} · ${t('app.name')}`
  }, [t])

  return (
    <>
      <PageHeader title={t('connections.shopify.returnTitle')} />
      {error ? (
        <div className="flex max-w-2xl flex-col items-start gap-4">
          <FormAlert tone="error">{error}</FormAlert>
          <Button asChild variant="secondary">
            <Link to="/connections">{t('connections.shopify.startAgain')}</Link>
          </Button>
        </div>
      ) : (
        <p role="status" className="m-0 text-body text-ink">
          {t('connections.shopify.working')}
        </p>
      )}
    </>
  )
}
