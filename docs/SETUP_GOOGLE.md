# Setting up Google: sign-in and Google Sheets

This guide covers "Continue with Google" (M3) and the Google Sheets connector (M6). Both use the same Google
Cloud project and OAuth client. Part A is sign-in; Part B is Sheets. Until you add credentials, both work locally
through stand-ins and nothing goes to Google.

# Part A: Google sign-in

"Continue with Google" works in three modes. The API picks one when it starts:

| `GOOGLE_OAUTH_CLIENT_ID` | `APP_ENV` | What happens |
|---|---|---|
| set | any | Real Google sign-in (this guide). |
| empty | `development` | A **stand-in** page served by the API itself: type any email address and you are "signed in with Google". The sign-in screen says "Development: Google is simulated on this computer." Nothing goes to Google. |
| empty | `test` / `production` | Google sign-in is off. The button is hidden. |

The stand-in refuses to start when `APP_ENV=production`.

The Google Sheets connector (Part B) uses the same Google Cloud project, but asks for different scopes through a
separate consent step. Sign-in only asks for `openid email profile`.

## 1. Create the OAuth client

1. Open <https://console.cloud.google.com/>, then create a project or pick one (for example "Analyst").
2. **APIs & Services → OAuth consent screen** (Google Auth Platform → Branding / Audience / Data access):
   - User type: **External**.
   - App name, support email and developer contact: your own details.
   - Scopes: `openid`, `.../auth/userinfo.email` and `.../auth/userinfo.profile`. These are basic scopes and
     need no Google review.
   - While the app is in **Testing**, add your own Google account under **Test users**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorised redirect URI: `http://localhost:8000/auth/google/callback`. In production, use
     `https://<your API host>/auth/google/callback`, which is `API_BASE_URL` + `/auth/google/callback`.
   - No "Authorised JavaScript origins" are needed: the browser never talks to Google from our pages.
4. Copy the **Client ID** and **Client secret**.

## 2. Add them to `.env`

```
GOOGLE_OAUTH_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-...
```

Restart the API (`make dev`). On http://localhost:5173/signin, "Continue with Google" now goes to the real
Google account chooser, and the "simulated" note is gone.

## 3. Before a public launch

- Move the consent screen from **Testing** to **In production**. With only basic scopes this usually needs no
  verification, but Google may ask for a privacy policy and a home page on a verified domain. Both come with the
  marketing site (M10b).
- The Google Sheets connector (M6) uses `spreadsheets.readonly` and `drive.file`. `spreadsheets.readonly` is a
  sensitive scope, so **Google app verification is required** before people outside your test-user list can
  connect Sheets. Plan a few weeks for it.
- Keep the client secret only in the production secret store, never in the repository.

## How the flow is protected

- Authorization-code flow with **PKCE (S256)**, a random `state` bound to the browser by a short-lived cookie
  (stops login CSRF), and a random `nonce` checked inside the ID token.
- The ID token is checked against Google's published keys: signature (RS256), issuer, audience (our client
  id), expiry, issue time and nonce. Google must say the email address is verified.
- Linking: a Google account is linked to an existing account only when the email address matches and Google
  says it is verified. If the existing account had never confirmed its email, its password is removed when
  linking, because it may have been set by someone who does not own the address.
- Google sign-in does not skip our own two-step verification. Owners still enter a code.

# Part B: Google Sheets connector (M6)

## How it works

1. An Owner presses **Connect Google Sheets** on the Connections screen. The API sends the browser to Google's
   consent screen asking for **read-only** access: `spreadsheets.readonly` and `drive.file` (plus `openid email`,
   to label the connection with the Google account).
2. Google sends the browser back to `API_BASE_URL/connections/google/callback`, which forwards it to the web app
   (`/connections/google/return`). The web app finishes the sign-in with the API as the signed-in Owner.
3. The **Google Picker** opens in the browser. The Owner chooses spreadsheets; the app can read only those. It
   never lists the person's Drive.
4. Each visible tab of each chosen spreadsheet becomes a table. Data is read live when needed and reused for
   `CACHE_TTL_SECONDS` (default 5 minutes). After that, Drive's version number tells whether anything changed,
   so an unchanged spreadsheet is not downloaded again.

The refresh token is stored encrypted (envelope encryption, `MASTER_KEY` or KMS) and never shown or logged.
Removing the connection deletes it and tells Google to revoke it.

### Modes

| `GOOGLE_OAUTH_CLIENT_ID`, `_SECRET`, `GOOGLE_PICKER_API_KEY`, `GOOGLE_CLOUD_PROJECT_NUMBER` | `APP_ENV` | What happens |
|---|---|---|
| all four set | any | Real Google Sheets (steps below). |
| any missing | `development` | A **stand-in**: a consent page served by the API ("Google Sheets stand-in") and a Picker dialog in the web app listing demo spreadsheets. The data comes from `eval/fixtures/marketing_sheet.xlsx` and `zoho_budget_expenses.xlsx` (run `make fixtures` first), plus a built-in "Outlet sales (sample)". Nothing goes to Google. |
| any missing | `test` / `production` | Google Sheets is off. The button is disabled with a note. |

Automated tests never call Google either: they replay recorded Google answers from
`backend/tests/connectors/fixtures/google_sheets.json`.

## B1. Turn on the APIs

In the same Google Cloud project as Part A, open **APIs & Services → Library** and enable:

- **Google Sheets API**
- **Google Drive API** (used only for `files.get` on the picked files: their version number)
- **Google Picker API**

## B2. Add the scopes to the consent screen

**Google Auth Platform → Data access → Add or remove scopes**, add:

- `https://www.googleapis.com/auth/spreadsheets.readonly` ("See all your Google Sheets spreadsheets"). This
  is a **sensitive** scope.
- `https://www.googleapis.com/auth/drive.file` ("See, edit, create and delete only the specific Google Drive
  files you use with this app"). Google's wording mentions editing, but the app only reads: every request goes
  through a client that refuses anything but reads (BUILD_SPEC 6.3).

Do **not** add `.../auth/spreadsheets` or `.../auth/drive`. The API refuses any token that carries a write scope.

While the app is in **Testing**, only the Google accounts listed under **Audience → Test users** can connect.

## B3. Add the redirect URI and JavaScript origin

Open the OAuth client from Part A (**Clients → your Web application client**):

- **Authorised redirect URIs**: add `http://localhost:8000/connections/google/callback` (production:
  `https://<your API host>/connections/google/callback`, i.e. `API_BASE_URL` + `/connections/google/callback`).
  Keep the sign-in URI from Part A as well.
- **Authorised JavaScript origins**: add `http://localhost:5173` (production: your web app's origin,
  `APP_BASE_URL`). The Picker runs in the browser and needs this.

## B4. Create the Picker API key

**APIs & Services → Credentials → Create credentials → API key**, then **Edit API key**:

- **Application restrictions → Websites**: `http://localhost:5173/*` (production: your web app's origin).
- **API restrictions → Restrict key**: tick **Google Picker API** only.

This key is public by design (the browser sends it to Google); the restrictions are what protect it.

## B5. Find the project number

**IAM & Admin → Settings** (or the project picker) shows the **Project number**: digits only, such as
`123456789012`. It is not the project id. The Picker needs it so that files picked by a person become readable
with the `drive.file` scope.

## B6. Add the values to `.env`

```
GOOGLE_OAUTH_CLIENT_ID=1234567890-abc.apps.googleusercontent.com      # already there from Part A
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-...                                  # already there from Part A
GOOGLE_PICKER_API_KEY=AIza...
GOOGLE_CLOUD_PROJECT_NUMBER=123456789012
```

Restart with `make dev`. On http://localhost:5173/connections the note under **Connect Google Sheets** no longer
says "Development: Google is simulated", and the button opens Google's own consent screen and Picker.

## B7. Before a public launch

- `spreadsheets.readonly` is a sensitive scope: **Google app verification is required** before people outside
  your test-user list can connect Sheets. Google asks for a privacy policy, a home page on a verified domain
  (both come with the marketing site, M10b), a demo video of the consent flow and a justification for each scope.
  Plan a few weeks.
- Without verification, test users see an "unverified app" warning, and refresh tokens for apps in Testing expire
  after 7 days (connections then show "Needs sign-in").
- Restrict the Picker key to the production origin only.

## What the platform does to stay read-only (Sheets)

1. Read-only scopes only, checked after every token exchange and refresh; any write scope is refused and the
   token revoked.
2. Every request goes through `ReadOnlyHttpClient`: `GET` only, plus `POST` to Google's token and revoke
   endpoints. Anything else raises `ReadOnlyViolation` (also enforced as an httpx request hook).
3. A test suite tries writes through the connector and checks they are blocked
   (`backend/tests/security/test_connections_security.py`).
