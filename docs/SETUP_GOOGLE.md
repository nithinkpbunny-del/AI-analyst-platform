# Setting up Google sign-in

"Continue with Google" works in three modes. The API picks one when it starts:

| `GOOGLE_OAUTH_CLIENT_ID` | `APP_ENV` | What happens |
|---|---|---|
| set | any | Real Google sign-in (this guide). |
| empty | `development` | A **stand-in** page served by the API itself: type any email address and you are "signed in with Google". The sign-in screen says "Development: Google is simulated on this computer." Nothing goes to Google. |
| empty | `test` / `production` | Google sign-in is off. The button is hidden. |

The stand-in refuses to start when `APP_ENV=production`.

Later, the Google Sheets connector (M6) needs the same Google Cloud project, but it asks for different scopes
through a separate consent step. Sign-in only asks for `openid email profile`.

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
