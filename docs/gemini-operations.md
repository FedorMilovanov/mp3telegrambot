# Gemini operations contract

This document describes the production operating contract for Gemini in MP3Bot. It is intentionally stricter than a generic API integration guide because Factory and translation QA are expensive, quality-sensitive workloads.

## Model policy

- User-visible semantic routes use `gemini-3.8-flash` with `HIGH` thinking where the route supports explicit thinking configuration.
- Shorts Factory MAX stays on Gemini 3.8 / HIGH / three independent review passes. It must not downgrade to 3.7/3.6/3.5/Lite in response to provider errors.
- LiveDub QA and publication semantic copy use the current 3.8/HIGH route.
- Translation Editorial review currently owns a dedicated `gemini-3.7-flash` / HIGH contract. This is an explicit reviewer route, not an automatic fallback from Factory 3.8.
- `gemini-3.5-flash-lite` is reserved for mechanical utility work that does not write user-visible semantic content.

For local migration use:

```powershell
./scripts/migrate-gemini-38.ps1
```

## Failure domains

Do not treat every retryable HTTP error as the same problem.

### 503 / high demand = backend capacity

A 503 means the Gemini backend is temporarily unavailable or overloaded. Factory keeps the same client and, for uploaded analysis audio, the same remote upload while using one bounded application-owned retry window.

The number of configured API keys must not multiply a persistent 503 storm. A backend-capacity event is not evidence that every key or project has exhausted quota.

### 429 / RESOURCE_EXHAUSTED = quota or rate limit

A 429 is a different failure domain. Factory may move to another configured credential, but the quota response does not reset an already-consumed 503 retry window. This preserves credential failover without turning one expensive Factory pass into `number_of_keys × retry_window` network calls.

### Other retryable 5xx

`500`, `502` and `504` remain bounded transient/service failures. They can move to another configured client when the current stage still has retry budget, but they do not change the model or quality contract.

## API keys and Google Cloud projects

Gemini rate limits are applied **per Google Cloud project, not per API key**. Multiple keys from the same project therefore share the same RPM/TPM/RPD limits.

Do not assume that four configured values (`GEMINI_API_KEY`, `_2`, `_3`, `_4`) represent four independent quotas.

For each production credential, record outside the repository:

1. the Google AI Studio key entry;
2. Key Type (`Auth` vs `Standard`);
3. associated Google Cloud project;
4. project usage tier and billing state;
5. whether another configured MP3Bot key belongs to the same project.

Never commit that mapping, full API keys, service-account identifiers, billing identifiers or secret material to this repository.

### Optional local quota-domain labels

After the owner has verified which credentials share one Google Cloud project, Factory can be given **opaque local labels** for that relationship:

```dotenv
GEMINI_QUOTA_DOMAIN=factory-project-a
GEMINI_QUOTA_DOMAIN_2=factory-project-a
GEMINI_QUOTA_DOMAIN_3=factory-project-b
GEMINI_QUOTA_DOMAIN_4=
```

These values are not Google project IDs. Use an arbitrary local label containing only letters, digits, `.`, `_`, `:`, or `-`; never place a project number, project ID, API key, billing identifier, email address or other secret/identity value in the label.

Behavior is deliberately conservative:

- blank labels preserve the previous credential-failover behavior exactly;
- a generic `429 RESOURCE_EXHAUSTED` still tries the next configured credential even when labels match, because the error scope is not proven to be project-wide;
- only a quota response that explicitly identifies a project-scoped limit (for example a quota name containing `PerProject`) marks that local domain exhausted for the current Factory run;
- after such a project-scoped 429, later credentials carrying the same explicit label are skipped for that run, while credentials with another or unknown label remain eligible;
- labels and API keys are never included in Factory status messages or terminal error text; diagnostics report only attempted/skipped counts;
- this optimization is request-local and does not persist quota state across Factory runs.

Do not configure these labels by guessing from the API-key string. Complete the owner credential/project audit first.

Official references:

- API keys: https://ai.google.dev/gemini-api/docs/api-key
- Rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Billing and project/key relationship: https://ai.google.dev/gemini-api/docs/billing

## September 2026 Auth-key migration

Google's current Gemini API documentation states that new AI Studio keys are Auth keys by default and that Standard keys must be migrated to Auth keys in September 2026.

Production action:

1. Open the API Keys page in Google AI Studio.
2. Check the **Key Type** column for every credential used by MP3Bot.
3. Replace every remaining Standard key with a newly created Auth key.
4. Update `.env` / deployment secrets without committing the key.
5. Restart the bot and verify the startup/runtime diagnostics.
6. Run one small Gemini request and one Factory path that exercises the Files API if Factory uses uploaded analysis audio.
7. Revoke the old Standard key only after the new credential is verified.

The key string prefix alone is not the source of truth; verify Key Type and project association in AI Studio.

## Production diagnosis checklist

When Factory fails, capture the following before changing retry settings:

- exact git/build SHA and dirty state;
- Python version;
- `google-genai`, `faster-whisper` and `ctranslate2` versions;
- Factory model;
- configured Gemini client count;
- HTTP status (`429`, `503`, etc.);
- provider retry delay when present;
- attempted client number (`N/total`);
- same-project credential skip count when present;
- whether the failing stage was Files upload, Factory pass 1/3, 2/3 or 3/3.

Do **not** print or attach API keys or local quota-domain labels.

Interpretation:

- persistent `503` on one bounded window: provider/backend capacity problem;
- `429` with a documented project/model quota: quota/rate-limit problem;
- repeated `429` across keys that belong to one project: expected shared-project quota behavior, not evidence that key rotation is broken;
- authentication rejection after a key migration: verify Key Type, API restrictions and project association before modifying retry code.

## What not to do

- Do not increase retry counts just to hide 503/429 failures.
- Do not rotate every API key for a backend-wide 503.
- Do not downgrade Factory to a weaker model to make an outage appear successful.
- Do not assume separate API keys have separate quotas.
- Do not expose keys or quota-domain labels in startup logs, `/status`, CI artifacts or issue reports.
