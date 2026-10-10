# Troubleshooting — Health Checks, Logs, and Fixing Problems

This guide covers the day-to-day "is it working?" questions. For understanding how the project works, see [GETTING_STARTED.md](GETTING_STARTED.md).

---

## Table of Contents

- [The Health Check Script](#the-health-check-script)
- [How to Check Function Logs](#how-to-check-function-logs)
- [Oura Token Expiry](#oura-token-expiry)
- [Webhook Subscriptions Expiring](#webhook-subscriptions-expiring)
- [Backfilling Missing Data](#backfilling-missing-data)
- [Deploying Changes](#deploying-changes)
- [Troubleshooting](#troubleshooting)

---

## The Health Check Script

The fastest way to check if the pipeline is healthy — no Azure Portal needed:

```bash
python scripts/check_health.py          # check today + yesterday
python scripts/check_health.py --days 7 # check the last week
```

**Requires:** `az login` and a `.env` file with:

```
OURA_CLIENT_ID=your-oura-client-id
KEY_VAULT_URI=https://ourahrv-kv.vault.azure.net/
BLOB_ACCOUNT_URL=https://ourahrvstore.blob.core.windows.net
```

### What it checks

The script runs five checks in order:

| Check | What it verifies |
|-------|-----------------|
| **1. Environment variables** | Your `.env` has the required config |
| **2. Blob storage** | HRV + RHR JSON files exist for recent dates |
| **3. Oura API token** | The OAuth refresh token still works |
| **4. Webhook subscriptions** | Both `sleep/create` and `sleep/update` subscriptions are active |
| **5. Oura API connectivity** | Can actually fetch data from Oura's API |

### Example output — everything healthy

```
Oura HRV Pipeline Health Check
Checking 2 day(s) of data...

1. Environment variables
  OK  OURA_CLIENT_ID is set
  OK  KEY_VAULT_URI is set
  OK  BLOB_ACCOUNT_URL is set

2. Blob storage (recent data)
  OK  2026-08-21: HRV + RHR present (last updated 2026-08-21 06:32 UTC)
  OK  2026-08-20: HRV + RHR present (last updated 2026-08-20 06:18 UTC)

3. Oura API token
  OK  Token refresh succeeded (access token: eyJhbGciOiJ...)

4. Webhook subscriptions
  OK  sleep/create (id: abc123..., expires: 2026-09-15)
  OK  sleep/update (id: def456..., expires: 2026-09-15)

5. Oura API connectivity
  OK  Fetched 1 sleep session(s) for today

Summary
  All checks passed. Pipeline is healthy.
```

### Example output — token expired

```
3. Oura API token
  FAIL  Refresh token is dead (invalid_grant)

  Fix: Re-authorize with Oura:
    python scripts/oauth_bootstrap.py \
      --vault-uri https://ourahrv-kv.vault.azure.net/ \
      --client-id <your-client-id> --client-secret <your-client-secret>
```

### Example output — missing data

```
2. Blob storage (recent data)
  OK    2026-08-21: HRV + RHR present (last updated 2026-08-21 06:32 UTC)
  FAIL  2026-08-20: no data

  Fix: Backfill missing dates:
    python scripts/fill_missing.py 2026-08-20
```

Every failure includes a **Fix** section with the exact command to run.

---

## How to Check Function Logs

The health check script covers most situations, but sometimes you want to see *what the Function App itself is doing* — especially when debugging why a specific webhook didn't arrive or process correctly.

### Option 1: Azure CLI (from your terminal)

The simplest way — no browser needed.

**Stream logs in real time** (useful when testing — sync your ring and watch):

```bash
az webapp log tail --name ourahrv-func --resource-group rg-oura
```

This shows logs as they happen. Sync your ring in the Oura app and you should see output like:

```
Processing sleep/create for id=abc123...
Wrote hrv/2026/08/2026-08-21_sleep-hrv.json
Wrote rhr/2026/08/2026-08-21_sleep-rhr.json
```

Press `Ctrl+C` to stop watching.

**Check recent function executions:**

```bash
# See the last 10 log entries
az monitor app-insights events show \
  --app ourahrv-insights \
  --resource-group rg-oura \
  --type traces \
  --order-by timestamp \
  --top 10
```

### Option 2: Azure Portal (more visual)

1. Go to [portal.azure.com](https://portal.azure.com)
2. Navigate to your Function App (`ourahrv-func`)
3. In the left sidebar, click **Functions**
4. Click **webhook_receiver** (or **renew_subscriptions**)
5. Click **Monitor** → **Invocations** tab
6. You'll see a list of every time the function ran, with status (success/failure) and timestamps
7. Click any row to see the detailed logs for that run — all the `logger.info()` and `logger.warning()` messages

### Option 3: Application Insights queries (most powerful)

For searching across many invocations or finding specific errors.

1. In the Azure Portal, go to your Application Insights resource (`ourahrv-insights`)
2. Click **Logs** in the left sidebar (opens a query editor)
3. Run queries like:

```
-- See all recent function activity
traces
| order by timestamp desc
| take 50

-- See only successful blob writes
traces
| where message contains "Wrote"
| order by timestamp desc

-- See errors and warnings only
traces
| where severityLevel >= 2
| order by timestamp desc

-- Check for token problems
traces
| where message contains "invalid_grant" or message contains "HMAC verification failed"
| order by timestamp desc

-- See subscription renewals
traces
| where message contains "Renewed subscription" or message contains "No webhook subscriptions"
| order by timestamp desc
```

The query language is called **KQL** (Kusto Query Language). It reads like English — `|` is "then", `where` filters rows, `order by` sorts, `take` limits results. You can mostly guess the syntax.

### Which to use when

| Situation | Best option |
|-----------|-------------|
| "Did last night's data come through?" | `python scripts/check_health.py` |
| "I'm syncing right now, let me watch" | `az webapp log tail` in terminal |
| "Something broke, show me details" | Azure Portal → Function → Monitor |
| "Search for a specific error across days" | Application Insights → Logs (KQL) |

---

## Oura Token Expiry

### What happens

Oura uses OAuth2 with refresh tokens. Here's the lifecycle:

1. **You authorize** (via `oauth_bootstrap.py`) → you get a refresh token
2. **Every webhook** → the Function uses the refresh token to get a short-lived access token → calls the Oura API
3. **Sometimes** → Oura returns a *new* refresh token alongside the access token → the Function automatically saves it to Key Vault
4. **Eventually** → the refresh token expires or gets revoked → Oura returns `invalid_grant` → the Function can't get an access token → data stops flowing

Step 3 is automatic — you'll never notice it. Step 4 requires manual intervention.

### How you'll know

**Best way:** Run the health check:

```bash
python scripts/check_health.py
```

If the token is dead, check 3 will show:

```
3. Oura API token
  FAIL  Refresh token is dead (invalid_grant)
```

**Other signs:**
- Your Apple Shortcut stops finding new data
- The health check shows missing blob data for recent dates
- If you check Function logs (see above), you'll see: `"Oura refresh token is dead (invalid_grant). Run scripts/oauth_bootstrap.py to re-authorize."`

### How to fix it

Run the bootstrap script to re-authorize:

```bash
python scripts/oauth_bootstrap.py \
  --vault-uri https://ourahrv-kv.vault.azure.net/ \
  --client-id <your-oura-client-id> \
  --client-secret <your-oura-client-secret>
```

This opens a browser where you log into Oura and grant access. The script stores fresh tokens in Key Vault. The Function picks them up automatically on the next webhook — no redeployment needed.

After re-authorizing, backfill any dates you missed:

```bash
# Check what's missing
python scripts/check_health.py --days 7

# Backfill specific dates
python scripts/fill_missing.py 2026-08-19
python scripts/fill_missing.py 2026-08-20
```

### How often does this happen?

It varies. Oura doesn't publish exactly when refresh tokens expire, but in practice it's months between re-authorizations. When in doubt, run the health check.

---

## Webhook Subscriptions Expiring

### What happens

Oura webhook subscriptions have an expiration date. If they expire, Oura simply stops sending webhooks — no error, no notification. Your Function still works; it just never gets called.

This is why the `renew_subscriptions` timer Function runs daily at noon UTC — it lists all subscriptions and renews each one.

### How you'll know

**Best way:** The health check shows subscription status:

```
4. Webhook subscriptions
  OK  sleep/create (id: abc123..., expires: 2026-09-15)
  OK  sleep/update (id: def456..., expires: 2026-09-15)
```

If subscriptions have expired:

```
4. Webhook subscriptions
  FAIL  No webhook subscriptions found

  Fix: Re-create subscriptions:
    python scripts/create_subscription.py \
      --callback-url https://ourahrv-func.azurewebsites.net/api/webhook \
      --client-id <your-client-id> --client-secret <your-client-secret> \
      --verification-token <your-token>
```

### How to fix it

Re-create the subscriptions:

```bash
python scripts/create_subscription.py \
  --callback-url https://ourahrv-func.azurewebsites.net/api/webhook \
  --client-id <your-oura-client-id> \
  --client-secret <your-oura-client-secret> \
  --verification-token <your-chosen-token>
```

Then run the same command again with `--data-type daily_readiness` for the readiness subscriptions.

The Function must be deployed and reachable — Oura sends a verification challenge during registration.

### Why would they expire if we renew daily?

Usually they don't — the daily renewal keeps them alive. But if the Function App was stopped, the timer trigger couldn't fire, and the subscriptions could lapse. This is rare.

---

## Backfilling Missing Data

### Single date

```bash
python scripts/fill_missing.py 2026-08-19
```

Checks blob storage first — only fetches and uploads what's actually missing. Safe to run multiple times.

### Date range

```bash
python scripts/backfill.py --start 2025-01-01 --end 2026-08-19
python scripts/backfill.py --start 2025-01-01 --end 2026-08-19 --metrics hrv,rhr,sleep,readiness
```

Fetches in 30-day chunks. By default writes only sleep and readiness files (`--metrics sleep,readiness`), so existing HRV/RHR files aren't touched unless you ask. Naps are skipped. Days with no data are skipped and counted in the summary at the end — that's normal for days you didn't wear the ring.

Uses Key Vault for Oura credentials, same as production (needs `az login`, `OURA_CLIENT_ID` and `KEY_VAULT_URI`). This keeps the refresh token in one place so a backfill can't break the live pipeline's token.

### Local testing (no blob upload)

Both scripts accept `--local` to write to `test_output/` instead of blob storage:

```bash
python scripts/fill_missing.py 2026-08-19 --local
```

---

## Deploying Changes

After making code changes:

```bash
# 1. Run tests
uv run python -m pytest tests/ -v

# 2. Regenerate requirements.txt (if dependencies changed)
uv export --no-hashes > requirements.txt

# 3. Deploy
func azure functionapp publish ourahrv-func --python
```

The Function App picks up the new code within a minute. No downtime — Azure swaps the code atomically.

---

## Troubleshooting

Quick-reference for common problems. For each one: what you see, why it happens, and how to fix it.

### "My Apple Shortcut isn't finding today's data"

**Check:** Run `python scripts/check_health.py`. This tells you exactly where the problem is — missing blob, dead token, expired subscription, etc.

**Quick manual check:** Visit the blob URL directly in a browser:
```
https://ourahrvstore.blob.core.windows.net/oura-data/hrv/2026/08/2026-08-21_sleep-hrv.json
```
If you see JSON data, the blob exists and the problem is in the Shortcut (wrong URL). If you get a 404, the webhook didn't fire or failed.

### "HMAC verification failed" in logs

**Why:** Either someone is hitting your webhook URL with random requests (this is normal on the internet — ignore it), or the client secret in Key Vault doesn't match the one registered with Oura.

**Fix:** If you're seeing it on *every* request (not just occasional noise), re-run `oauth_bootstrap.py` to resync the client secret.

### "No valid HRV data for {date}"

**Why:** Oura's sleep document for that night didn't contain usable HRV data. This happens with very short sleep sessions, naps, or when the ring wasn't worn properly.

**Fix:** Usually resolves on its own when Oura sends a `sleep/update` event with revised data. If the date is old, try `python scripts/fill_missing.py <date>` — the updated data might be available now.

### "Skipping nap/non-overnight sleep" in logs

**Why:** Oura sends a separate `sleep` webhook for naps. Only overnight sleep (`type: long_sleep`) is saved, so a nap never overwrites that day's HRV, RHR or sleep files.

**Fix:** Nothing — this is expected.

### "No valid readiness data for {date}"

**Why:** Oura hasn't calculated a readiness score for that day yet (the score is empty).

**Fix:** Usually resolves on its own with a `daily_readiness/update` event. For an old date: `python scripts/backfill.py --start <date> --end <date> --metrics readiness`.

### "No webhook subscriptions found" in logs

**Why:** The daily renewal timer ran but found no subscriptions to renew. They either expired (Function was stopped for a while) or were never created.

**Fix:** Re-create them with `scripts/create_subscription.py` (see [Webhook Subscriptions Expiring](#webhook-subscriptions-expiring)).

### Cold start delays

**Why:** Azure Consumption plan deallocates your Function when idle. The first request after a quiet period takes a few extra seconds to spin up.

**Impact:** None. Oura's webhook delivery is asynchronous — it doesn't care if your response takes 5 seconds instead of 0.5. Oura also retries on failure, so even a timeout on a cold start usually recovers.

### "Timer is past due" warning

**Why:** The daily subscription renewal timer was supposed to fire while the Function App was stopped or being deployed. Azure runs it as soon as it can.

**Impact:** None. The warning is informational — the renewal still runs. This is normal after a deployment.

### Rate limiting from Oura

**Why:** Oura's API has rate limits. You'll see HTTP 429 responses.

**Fix:** Wait a minute and retry. The `fill_missing.py` and `backfill.py` scripts handle 429s automatically with a wait-and-retry. For the webhook Function, this is extremely rare since it only fires ~once per day.
