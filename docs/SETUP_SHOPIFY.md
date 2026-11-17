# Connecting Shopify

The app reads your store's orders, order items, customers, products and variants. It asks Shopify only for **read**
access and sends only GraphQL **queries** (plus Shopify's read-only bulk export for large histories); it can never
change anything in your store. It does not read customer names, e-mail addresses, phone numbers or street addresses.

## Part A: trying it without a Shopify store (development only)

Leave `SHOPIFY_API_KEY` and `SHOPIFY_API_SECRET` empty and keep `APP_ENV=development`.

1. `make fixtures` (once), then `make dev`.
2. Sign in as an Owner and open **Connections > Add data > Connect Shopify**.
3. Type any store address, e.g. `urbancart-demo`, and press **Continue to Shopify**.
4. A local page, **Shopify stand-in**, lists the read access the app asks for. Tick "Also allow orders older than
   60 days" to see the full history, then press **Install app**.
5. You land on the new connection. The demo orders are 28,540 rows, so the first read uses a bulk export (as a big
   real store would); it takes a few seconds.

## Part B: a real Shopify app (needed for real stores)

1. Create an app in the **Shopify Dev Dashboard** (or Partners). For one store of your own a **custom distribution**
   app is enough; for many merchants it must be a **public** app (Shopify reviews it).
2. App settings:
   - **App URL:** your web app, e.g. `https://app.example.in/connections`.
   - **Allowed redirection URL:** `<API_BASE_URL>/connections/shopify/callback`, e.g.
     `http://localhost:8000/connections/shopify/callback` locally.
   - **Access scopes:** `read_orders`, `read_products`, `read_customers`, `read_inventory`. Request
     `read_all_orders` from Shopify too (approval needed) if you want orders older than 60 days; once approved, add it
     to `SHOPIFY_SCOPES`.
   - **Protected customer data** (public apps): request level 1 access (customer ids and order data) and the **city,
     province and country** fields only. The app never asks for names, e-mail addresses, phone numbers or street
     addresses.
3. Copy the **Client ID** and **Client secret** into `.env`:

   ```
   SHOPIFY_API_KEY=<client id>
   SHOPIFY_API_SECRET=<client secret>
   ```

   Restart the API and the worker (`make dev`). The stand-in is then off and "Connect Shopify" goes to Shopify.
4. Connect: **Connections > Add data > Connect Shopify**, type the store address, approve on Shopify. The app checks
   Shopify's signature on the way back, checks that only read access was granted, reads the shop name once, and only
   then stores the access token (encrypted).

## Good to know

- **Orders older than 60 days** need Shopify's `read_all_orders` access. The connection page says when it is missing.
- **First read:** orders go back `SHOPIFY_HISTORY_DAYS` (730). A table with more than `SHOPIFY_BULK_THRESHOLD` (1,000)
  rows is read with one bulk export; after that only rows updated since the last read are fetched.
- **Amounts are in rupees**, exactly as Shopify sends them (Razorpay sends paise; the dictionary knows both).
- **Removing** the connection deletes the stored token and the cached data at once. To stop the app completely,
  also **uninstall it in the Shopify admin** (Settings > Apps): uninstalling is a change in your store, which this
  app never makes.
- **Shopify stopped sharing data** (token revoked, app uninstalled): open the connection and press **Sign in to
  Shopify again**.
- **API version:** `SHOPIFY_API_VERSION` (2026-07). Shopify supports each version for about a year; move it forward
  before then.
- **Before launch, check against a real store:** the connector and its tests were built against a local imitation
  of Shopify's API (no real store was used). Connect a development store once and compare a few orders with the
  Shopify admin (totals, discounts, refunds, line items), and check that the bulk export finishes.
