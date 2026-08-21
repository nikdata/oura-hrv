# Gameplan: Convert `oura-hrv` from GitHub Actions → Azure Webhook + Blob Storage

**Audience:** Claude Code, working in the `nikdata/oura-hrv` repo.
**Author:** Handoff from planning session. Read this whole file before writing code.
**Last updated:** 2026-08-21
**Status:** Complete — all steps implemented and working in production.

---

## 0. TL;DR of the change

The problem being solved is **one specific thing: GitHub Actions scheduled runs are unreliable.** Cron-triggered workflows get skipped/delayed, which is why the old pipeline needed 7 runs/day and still required manual reruns. The Actions trigger is currently disabled for that reason.

The fix is to replace the **cron trigger with a push-based Oura webhook**, hosted on Azure. Oura notifies an always-on Azure Function ~30s after the phone syncs; the Function fetches the sleep document, extracts HRV + RHR using the **existing, unchanged** `data_processor.py` logic, and writes the JSON to **Azure Blob Storage** at a dated path. The Apple Shortcut (still manually triggered) reads from the Blob URL instead of the GitHub raw URL.

This is a **trigger + storage change, not a re-architecture of the data logic.** The HRV/RHR extraction transform is preserved byte-for-byte. The output JSON shape is preserved so the Apple Shortcut barely changes (only its base URL).

**Two Azure Functions total.** No GitHub Actions, no git-as-datastore, no file organizer, no GitHub PAT.

---

## 1. Context you need so you don't re-litigate decisions

These were decided deliberately in planning. Don't reopen them:

- **Why webhooks:** the failure was *scheduled execution*, not data retrieval. Webhooks push when data exists, removing the cron dependency entirely. That is the whole point.
- **Why Blob Storage (not git, not Table Storage):** the old repo committed per-night JSON files and ran an organizer to sort them into folders. Blob replaces this: the blob is named with its dated path at write time (pre-sorted, no organizer needed), and `sleep.update` events overwrite the same blob key for free (no commit conflicts, no dedup logic). Git-as-store, the organizer, and the GitHub PAT are all **removed** — not ported.
- **Why not Apple Health direct / read_api / owned-store:** out of scope. The goal is minimal change to the working Apple Shortcut. The Shortcut still reads a JSON file over HTTPS and writes to Health itself. We are NOT touching the iOS side beyond one URL.
- **Compute:** Azure Functions, **Consumption plan.** Cold start is fine — webhook delivery is async and Oura retries. Do NOT move to Premium/always-warm "to avoid cold starts"; it's unnecessary and costs ~$13-15+/mo.
- **Language:** Python, Azure Functions v2 programming model. Keep the repo's existing functional style — minimal classes.
- **Dependencies:** repo uses `uv` (`pyproject.toml` + `uv.lock`). Keep it. Generate `requirements.txt` for the Functions build from uv (`uv export --no-hashes > requirements.txt`) as a deploy step.

---

## 2. Auth: Oura OAuth is unchanged — token STORAGE moves to Key Vault

The existing Oura OAuth flow (authorization-code via the `example.com/callback` redirect, client ID/secret → refresh token) **stays as-is.** Oura's PAT deprecation is irrelevant — this repo never used an Oura PAT. (The old `REPO_SECRETS_TOKEN` was a *GitHub* PAT for writing to GitHub Secrets; it disappears with git-as-store.)

**Why the token can't stay in GitHub Secrets:** GitHub Secrets are only readable inside a GitHub Actions run. The Azure Function is not running in Actions, so it cannot read them at runtime. The token must live somewhere the Function can read at runtime → **Azure Key Vault.**

**Why Key Vault specifically (not Function app settings):** the Oura refresh token **rotates** — on refresh, Oura may return a new refresh token that must be persisted, or the chain breaks. Writing a rotated secret back to app settings requires the Function to call the Azure management API to edit its own config (hacky). Writing back to Key Vault is a clean supported operation. The write-back-on-rotation need is what makes Key Vault worth its ~cents/month.

### Two token paths — build BOTH

1. **Automatic (normal operation):** Function reads the refresh token from Key Vault → exchanges it at `https://api.ouraring.com/oauth/token` for an access token → if Oura returns a new refresh token, **writes it back to Key Vault.** Silent; no user involvement.
2. **Manual re-auth (when the chain fully dies):** refresh tokens eventually expire/revoke; when that happens no auto-refresh can recover it and the OAuth authorization flow must be re-run by hand. Provide `scripts/oauth_bootstrap.py` (also used for first-time seeding) that walks the auth-code flow locally, mints a fresh refresh token, and writes it to Key Vault.
   - **Make the failure distinguishable:** when auto-refresh fails, log clearly whether it's a transient error (retry) vs. a dead-chain `invalid_grant` (→ user must run `oauth_bootstrap.py`). Nikhil expects to do this periodically and needs to know *when*.

- Authorization URL: `https://cloud.ouraring.com/oauth/authorize`
- Token exchange: `https://api.ouraring.com/oauth/token`
- Keep the repo's current scopes (they already work). Only revisit if a `sleep` fetch returns 403.

**Webhook subscription auth is separate from data auth:** subscription-management endpoints use **`x-client-id` + `x-client-secret` headers** (app client credentials), NOT a Bearer token. The client secret also verifies the HMAC on incoming events, so the Function needs it at runtime (store in Key Vault too, or app settings via Key Vault reference).

---

## 3. Target architecture

```
Oura Cloud
  │  POST notification (~30s after phone sync; HMAC-signed via x-oura-signature)
  ▼
Azure Function: webhook_receiver  ──reads/writes──►  Key Vault (Oura refresh token, client secret)
  │  1. verify x-oura-signature HMAC (client secret, timing-safe, against RAW body)
  │  2. return 200 within a couple seconds
  │  3. call back to Oura for the sleep document (by id)
  │  4. extract HRV time series + RHR  (data_processor.py — UNCHANGED transform)
  │  5. write blob at dated path (overwrite-on-update)
  ▼
Azure Blob Storage (public-read container)
  hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json
  rhr/YYYY/MM/YYYY-MM-DD_sleep-rhr.json
  ▲
  │  GET  https://<account>.blob.core.windows.net/<container>/hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json
Apple Shortcut (HRV) + Apple Shortcut (RHR)  ──►  Apple Health   [manually triggered; only base URL changes]

Azure Function: renew_subscriptions  (timer, daily)  ──►  keeps Oura webhook subscription alive
```

---

## 4. Oura webhook specifics (verified 2026-08; RE-CONFIRM the ⚠️ items against live docs)

Live docs: `https://api.ouraring.com/v2/docs` (JS-rendered — open in a browser, not curl).

- **Subscription API:** `POST /v2/webhook/subscription` (+ `GET` list, `PUT` renew/update, `DELETE`). Auth via `x-client-id` / `x-client-secret` headers.
- **One subscription per `data_type` + `event_type`.** Subscribe to **`sleep` / `create` AND `sleep` / `update`** — Oura revises sleep documents hours later (updated HRV, corrected staging). The old 7×/day poll caught revisions; to match that, you must handle `update`, and overwrite the blob on update. Missing this = slightly worse data than today.
- **Notification is a pointer, not data.** Payload has `data_type`, `event_type`, and the object `id`. Make a follow-up authenticated call to fetch the actual `sleep` document.
- **HRV lives on the `sleep` document** under `hrv` (a `{ items, interval, timestamp }` time series), NOT on `daily_sleep`. The existing `data_processor.py` already knows this — preserve it.
- **Latency:** ~30s after the ring syncs from the phone. Nikhil still opens the Oura app to trigger the sync, same as today.
- **HMAC:** every event POST carries **`x-oura-signature`**; verify it using the **client secret** against the **raw, unaltered request body bytes** (don't re-serialize parsed JSON first). Timing-safe comparison. Reject on mismatch.
- ⚠️ **Subscription-creation verification handshake:** creating a subscription makes Oura call your callback URL with a **verification challenge you must echo back.** CONFIRM the exact mechanism against live docs (GET + query-param echo vs. JSON body). Common failure point — do not guess.
- ⚠️ **Confirm the follow-up fetch shape:** whether the notification `id` maps to `GET /v2/usercollection/sleep/{id}` directly or needs a dated range query.
- ⚠️ **Confirm HMAC encoding** (hex vs base64, SHA256 assumed) from the docs' webhook code example before implementing.

---

## 5. Blob layout & write behavior

- **Container:** one container, e.g. `oura-data`, **public read (blob-level)** so the Shortcut fetches over plain HTTPS with no auth — same trust model as the current public GitHub repo.
  - ⚠️ Public means anyone with the URL can read the HRV/RHR JSON. This matches today's public-repo exposure and is accepted. (Private + SAS is the locked-down alternative but SAS tokens expire → periodic Shortcut edits → not worth it here.)
- **Blob names (dated path, pre-sorted at write time):**
  - `hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json`
  - `rhr/YYYY/MM/YYYY-MM-DD_sleep-rhr.json`
  - `YYYY-MM-DD` = sleep date, local. The `YYYY/MM` prefix replaces the old folder organizer entirely.
- **Write = upload with overwrite.** `sleep.update` re-uploads the same blob name → last-write-wins. This replaces the old file-based dedup. No organizer, no skip-if-exists logic.
- **Content-Type:** set `application/json` on upload so the Shortcut's "Get Contents of URL" parses cleanly.

### The JSON output shape MUST be preserved exactly
The blob content must be **byte-shape-identical** to what the current repo emits, because the Apple Shortcut parses it unchanged. Verified against real repo fixtures (`data/hrv/2026/06/2026-06-01_sleep-hrv.json`, `data/rhr/.../2026-06-01_sleep-rhr.json`):

- **Top level is a bare JSON array** `[ {reading}, ... ]` — NOT wrapped in `{"data": [...]}`. The Shortcut's "Repeat with Each" iterates the array directly; an envelope breaks it.
- **HRV** = a long array (80-96 objects/night, one per 5-min interval). Each object, exact keys/types:
  ```json
  {
    "date": 1780267377,
    "date_readable": "2026-05-31 22:42:57",
    "timezone": "UTC",
    "timezone_offset": "+0000",
    "hrv": 18.0,
    "unit": "ms",
    "source": "oura_ring_rmssd",
    "measurement_type": "rMSSD"
  }
  ```
- **RHR** = an array with (typically) a **single** object:
  ```json
  {
    "date": 1780292577,
    "date_readable": "2026-06-01 05:42:57",
    "timezone": "UTC",
    "timezone_offset": "+0000",
    "rhr": 59,
    "unit": "bpm",
    "source": "oura_ring_sleep",
    "measurement_type": "lowest_heart_rate"
  }
  ```
- **Type details that must not drift:** `hrv` is a float (`18.0`), `rhr` is an int (`59`); `date` is a Unix epoch int; `date_readable` is a string `"YYYY-MM-DD HH:MM:SS"`.
- **⚠️ Timezone is per-record and comes straight from Oura — pass it through untouched.** Each record's `timezone` / `timezone_offset` reflect the zone the ring was worn in that night, so they **vary by record and by night** (home vs. travel, across DST). The `2026-06-01` fixture happens to show `UTC` / `+0000`, but that is NOT a fixed value — do not treat UTC as canonical. Do NOT normalize, convert, or localize these to any single zone (not UTC, not Central); emit exactly what Oura returns and render `date_readable` in that same zone. This per-record pass-through is the "travel & DST proof" behavior — flattening it to one zone shifts readings to the wrong time in Apple Health. The old README's `CDT` examples and this fixture's `UTC` are both just *samples* of what varies.
- Commit real copies of both files as the `example/` fixtures and add a `pytest` that diffs new output against them, asserting the key set and value types. For timezone, assert only that `timezone`/`timezone_offset` are present and non-empty and that `date_readable` matches the record's offset — do NOT assert a specific zone (it varies per night; see the warning above).

---

## 6. 🚨 DO NOT "fix" the HRV metric — RMSSD stays RMSSD

The Shortcut writes Oura HRV into Apple Health's **RMSSD** field (`Heart Rate Variability RMSSD`). This is deliberate and correct.
- Oura measures **rMSSD**; Apple Health's default HRV field is **SDNN**. Different metrics, **not interconvertible.**
- Do NOT convert rMSSD→SDNN, do NOT scale, do NOT write to the SDNN field, do NOT "normalize" units. The `hrv` value is milliseconds of rMSSD and passes through untouched end-to-end.
- Keep `measurement_type: "rMSSD"` and `source: "oura_ring_rmssd"` for provenance.
- If you find yourself writing HRV conversion math, stop — that's the bug, not the feature.

---

## 7. Subscription renewal (don't skip — silent failure otherwise)

Oura webhook subscriptions **expire.** If not renewed, delivery stops silently — recreating the exact reliability failure we're escaping, but quieter.
- **`renew_subscriptions` timer Function** (daily): list subscriptions (`GET /v2/webhook/subscription`), renew any nearing expiry (`PUT`/renew per live docs).
- Log renewals; alert on failure (log-based alert, or email via Logic App / SMTP). A failed renewal is invisible otherwise.
- **Bootstrap:** `scripts/create_subscription.py` registers the initial `sleep`/`create` + `sleep`/`update` subscriptions pointing at the deployed receiver URL. Document the exact invocation in the README. (Deploy the receiver first — the callback URL must be reachable before the subscription can be verified.)

---

## 8. Repo restructure

**Remove:**
- `.github/workflows/process-hrv.yml`
- `.github/workflows/organize-data.yml`
- `src/data_organizer.py` — its job (sorting committed files into folders) no longer exists; the blob path prefix replaces it. **Delete, don't port.**
- The committed `data/` directory as a data store (keep a tiny `example/` fixture for tests/docs only).
- GitHub-Secrets / GitHub-PAT token logic in `oura_client.py`.

**Keep / adapt:**
- `data_processor.py` — **the crown jewel; transform unchanged.** Only its input (now a sleep doc from the webhook callback) and output (now a blob) change. The extraction in the middle is preserved and test-verified against the `example/` fixture.
- `oura_client.py` — strip GitHub logic; token storage → Key Vault; keep OAuth refresh + 401-recovery request handling.

**Add:**
```
function_app.py                # Azure Functions v2 entry
functions/
  webhook_receiver.py          # HTTP trigger: HMAC verify + challenge + callback + blob write
  renew_subscriptions.py       # Timer trigger (daily): keep subscription alive
core/
  oura_client.py               # (adapted) OAuth2 + Key Vault token mgmt + API calls
  data_processor.py            # (ported, UNCHANGED transform) HRV/RHR extraction
  blob_store.py                # blob upload (overwrite) + content-type + path builder
  security.py                  # HMAC verify (timing-safe) + challenge handling
host.json
local.settings.json.example
infra/                         # Bicep (or az CLI) — see §9
scripts/
  oauth_bootstrap.py           # first-time seed AND manual re-auth → writes token to Key Vault
  create_subscription.py       # one-time: register sleep/create + sleep/update subscriptions
README.md                      # rewritten (see §11)
gameplan.md                    # this file
```

---

## 9. Infrastructure (Azure)

Provision via **Bicep** in `infra/` (or an `az` CLI script if Bicep is overkill):
- Resource group.
- **Storage account** — backs both the Function runtime AND the Blob data container (same account is fine; dedicated container `oura-data` with public blob access for the data container).
- **Function App** — Consumption (or Flex Consumption), Python, with a **system-assigned managed identity**.
- **Key Vault** — grant the Function's managed identity `get` + `set` on secrets (set is required for token write-back on rotation). Secrets: Oura refresh token, Oura client secret.
- **App settings:** `KEY_VAULT_URI`, `OURA_CLIENT_ID`, blob account/container config; client secret as a **Key Vault reference**, never plaintext.
- **Application Insights — cap it.** ⚠️ Default App Insights ingestion can quietly cost more than everything else combined. Enable minimal sampling / a daily cap, or skip it. This is the one line item that can turn "~free" into a few dollars.

**Callback URL** = deployed `webhook_receiver` Function URL (HTTPS, public). Deploy before running `create_subscription.py`.

---

## 10. Build order — ALL COMPLETE

1. [x] **Scaffold** the Functions app; deploy a trivial HTTP 200 to Azure; confirm the public HTTPS URL.
2. [x] **Key Vault + OAuth bootstrap:** run `scripts/oauth_bootstrap.py` locally to seed the refresh token into Key Vault. Confirm the Function reads it (managed identity) and can fetch a known `sleep` document.
3. [x] **Port `data_processor.py`;** prove byte-shape-identical output vs. the `example/` fixture with a `pytest`.
4. [x] **`blob_store.py`:** upload-with-overwrite at the dated path, `application/json`, public-read. Verify the blob URL is fetchable in a browser.
5. [x] **`webhook_receiver`:** HMAC verify (unit-test with a crafted signed payload) + challenge handshake (per live docs) + callback fetch + blob write.
6. [x] **`create_subscription.py`:** register `sleep`/`create` + `sleep`/`update` at the deployed receiver; watch the verification handshake succeed.
7. [x] **End-to-end:** sync the ring via the Oura app → confirm a real notification lands → blob appears at the dated path within ~a minute. Test an `update` overwrites.
8. [x] **`renew_subscriptions`** timer (daily) + failure alerting.
9. [x] **Update the two Apple Shortcuts:** swap the GitHub raw base URL for the Blob base URL. Date-path building is unchanged in spirit — Nikhil uses Format Date + concatenated text; the path is now `hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json` (and `rhr/...`). Everything downstream (Get Dictionary → Repeat → Log Health Sample) is untouched.
10. [x] **Rip out** GitHub Actions workflows, `data_organizer.py`, committed `data/`, GitHub-PAT logic. Rewrite README.

---

## 11. README rewrite

The current README documents the GitHub Actions pipeline — most is now wrong. Rewrite to cover: the (unchanged) Oura OAuth setup now seeding Key Vault; Azure resource provisioning (Bicep); Key Vault secret seeding + the manual re-auth path; subscription creation + the renewal caveat; the new Blob base URL for the Shortcut; and the preserved RMSSD semantics. Keep the "Technical Background" rMSSD-vs-SDNN section — still accurate, explains §6.

---

## 12. Open questions — RESOLVED

All resolved during implementation and confirmed against live Oura API behavior:

1. ~~Subscription-creation **verification challenge** format~~ — **GET with query params.** Oura sends `GET /api/webhook?verification_token=<token>&challenge=<challenge>`. We echo back `{"challenge": "<challenge>"}` as JSON. Implemented in `webhook_receiver.py`.
2. ~~Whether the notification `id` fetches via `GET /v2/usercollection/sleep/{id}`~~ — **Yes, direct ID fetch works.** `GET /v2/usercollection/sleep/{id}` returns the full sleep document. Implemented in `oura_client.py:fetch_sleep_document()`.
3. ~~`x-oura-signature` **HMAC algorithm + encoding**~~ — **SHA256, hex-encoded, uppercased.** Oura signs `timestamp + body` with the client secret. Implemented in `security.py:verify_hmac()`.
4. ~~Exact **scope** required~~ — **`personal daily sleep heartrate`** works. No 403s encountered. Set in `oauth_bootstrap.py`.

---

## 13. Explicit non-goals (don't scope-creep)

- No multi-provider (Garmin/Whoop). Oura sleep only.
- No Table Storage, Postgres, dashboard, or recovery-score computation.
- No `read_api` Function — the Shortcut reads the blob directly.
- No writing to Apple Health from Azure — the Shortcut remains the Health writer, manually triggered. iOS side is untouched beyond the base URL.
- No git-as-datastore, no file organizer, no GitHub PAT — all removed.
- Do NOT re-architect Oura OAuth — only its token storage moved.

---

## 14. Approximate cost

For single-user, ~once-daily traffic: **effectively free, realistically under $1/month**, provided two guardrails hold:
- **Stay on Consumption** (pay-per-execution). Functions free grant (1M exec + 400k GB-s) covers this many-fold. Premium/always-warm = ~$13-15+/mo for warmth you don't need.
- **Cap Application Insights** ingestion (§9) — the one thing that can quietly dominate the bill.

Blob Storage: a few thousand tiny JSON blobs/yr → cents. 
Key Vault: a handful of secret ops per run → cents. Bandwidth: trivial. This is more than GitHub Actions cost ($0), but buys the reliability that was the entire motivation.