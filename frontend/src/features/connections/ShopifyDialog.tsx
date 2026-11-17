import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FormAlert } from '../../components/FormAlert'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent } from '../../components/ui/dialog'
import { Field } from '../../components/ui/input'
import { ApiError } from '../../lib/api'
import { connectionsApi } from '../../lib/connectionsApi'

// The same forms the server accepts: "your-store", "your-store.myshopify.com" or an admin address.
const SHOP =
  /^(https?:\/\/)?([a-z0-9][a-z0-9-]*(\.myshopify\.com)?|admin\.shopify\.com\/store\/[a-z0-9-]+)(\/.*)?$/i

/**
 * Connect a Shopify store: the Owner enters the store address and is sent to Shopify to approve read-only access.
 * Shopify sends the browser back to the API, then to `/connections/shopify/return`. Loaded on demand.
 */
export function ShopifyDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const [shop, setShop] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [problem, setProblem] = useState<string | null>(null)

  const go = useMutation({
    mutationFn: () => connectionsApi.shopifyStart({ shop: shop.trim() }),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    onError: (caught) => {
      if (caught instanceof ApiError && caught.fields.shop) setError(caught.fields.shop)
      else setProblem(caught instanceof ApiError ? caught.message : t('connections.shopify.failed'))
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const next = SHOP.test(shop.trim()) ? undefined : t('connections.shopify.shopInvalid')
    setError(next)
    setProblem(null)
    if (!next) go.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={t('connections.shopify.title')}
        description={t('connections.shopify.intro')}
      >
        <form onSubmit={submit} className="mt-4 flex flex-col gap-4" noValidate>
          {problem ? <FormAlert tone="error">{problem}</FormAlert> : null}
          <Field
            label={t('connections.shopify.shop')}
            hint={t('connections.shopify.shopHint')}
            error={error}
            value={shop}
            onChange={(event) => setShop(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            inputMode="url"
            placeholder="your-store.myshopify.com"
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={go.isPending || go.isSuccess}>
              {go.isPending || go.isSuccess
                ? t('connections.shopify.opening')
                : t('connections.shopify.start')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
