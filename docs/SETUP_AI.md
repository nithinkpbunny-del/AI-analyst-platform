# Setting up the AI providers

The platform talks to **Google Gemini** (the default) or **Anthropic Claude** through one provider layer
(`backend/app/llm/`, BUILD_SPEC 7.2). There are two kinds of keys:

| Key | Where it lives | Used by |
|---|---|---|
| The **platform key** | `.env` (`GEMINI_API_KEY` or `ANTHROPIC_API_KEY`) | Workspaces on **Managed AI**, up to `MANAGED_MONTHLY_QUESTION_LIMIT` questions a month each |
| A workspace's **own key** (BYOK) | Pasted by an Owner in **Settings → AI plan**; encrypted in the `secrets` table | That workspace only, with no platform limit |

Tests and CI never call a provider: they use the fake client (`app/llm/fake.py`).

## Gemini (default)

1. Open <https://aistudio.google.com/apikey> and sign in with a Google account.
2. **Create API key** (pick or create a Google Cloud project). The free tier is fine for development. For
   production use a project with billing turned on: Managed AI promises customers a paid tier, which does not
   train on their data (BUILD_SPEC 6.6).
3. Put it in `.env`:
   ```
   LLM_DEFAULT_PROVIDER=gemini
   GEMINI_API_KEY=<your key>
   GEMINI_API_VERSION=v1beta
   GEMINI_DEFAULT_MODEL=
   ```
   Leave `GEMINI_DEFAULT_MODEL` empty unless you need a specific model. The app lists the models your key can use
   and picks the newest stable Flash model. If a model you name is listed but refused (Google keeps listing models
   that are "no longer available to new users"), the app switches to the next best one by itself and logs a
   warning.
4. Check it: `make llm-check`. You should see the number of models, a tool call, the tool result going back and a
   one-sentence answer, then `Round trip OK.`

## Claude (optional)

1. Open <https://platform.claude.com/> → **API keys** → **Create key**.
2. Put it in `.env` (and set `LLM_DEFAULT_PROVIDER=claude` if Managed AI should use Claude):
   ```
   ANTHROPIC_API_KEY=<your key>
   CLAUDE_DEFAULT_MODEL=claude-opus-5-5
   ```
3. Check it: `make llm-check PROVIDER=claude`.

## A workspace's own key (BYOK)

An Owner opens **Settings → AI plan**, chooses **Your own key**, picks the provider and pastes the key. The key is
checked with the provider first (a model list call: no tokens used), then encrypted with a per-secret data key
(AES-256-GCM) wrapped by `MASTER_KEY`. After that only the provider and the last 4 characters are ever shown.
Removing the key deletes it and switches the workspace back to Managed AI.

## Production notes

- **Encryption keys.** Development wraps data keys with `MASTER_KEY`. In production set `KMS_KEY_ID` (an AWS KMS
  key in `ap-south-1`); data keys are then wrapped by KMS with the secret's identity as encryption context. Keep
  `MASTER_KEY` set as well: it still protects two-step secrets and recovery-code hashes.
- **Prices.** Estimated costs come from `backend/app/llm/pricing.py`. Models missing from that table show
  "not known" in the usage meter; add their list prices there when providers publish them.
