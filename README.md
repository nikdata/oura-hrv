# Oura HRV & RHR to Apple Health

Transfers detailed Heart Rate Variability (HRV) time series and Resting Heart Rate (RHR) from your Oura Ring to Apple Health via Azure Functions + an Apple Shortcut.

**How it works:** Oura sends a webhook ~30s after your ring syncs. An Azure Function receives it, fetches the sleep document, extracts HRV + RHR, and writes the JSON to Azure Blob Storage. Your Apple Shortcut reads the blob and logs the data into Apple Health.

> **New to this project?** Start with these guides:
> - [GETTING_STARTED.md](GETTING_STARTED.md) — How everything works (Azure Functions basics, architecture, what every file does)
> - [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Health checks, checking logs, troubleshooting, and fixing common problems

## Technical Background

### HRV: rMSSD stays rMSSD

- **Oura Ring** measures **rMSSD** (Root Mean Square of Successive Differences) — optimized for sleep analysis
- **Apple Watch** measures **SDNN** (Standard Deviation of NN intervals) — general purpose
- These are different metrics and are **not interconvertible**. This pipeline writes to Apple Health's **RMSSD** field, not SDNN
- Oura values are typically higher than Apple Watch readings and not directly comparable

### RHR Methodology

Captures the **lowest heart rate** during sleep with the actual timestamp when it occurred (not midnight or wake-up time). If the same lowest value occurs multiple times, uses the last occurrence.

## Architecture

```
Oura Cloud
  │  POST webhook (~30s after phone sync)
  ▼
Azure Function: webhook_receiver
  │  1. Verify HMAC signature
  │  2. Fetch the sleep or daily_readiness document from Oura API
  │  3. sleep: skip naps; extract HRV time series + RHR (unchanged
  │     transform) + bedtime/wake time/total sleep
  │     daily_readiness: extract the readiness score
  │  4. Write to Blob Storage at dated path
  ▼
Azure Blob Storage (public-read)
  hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json
  rhr/YYYY/MM/YYYY-MM-DD_sleep-rhr.json
  sleep/YYYY/MM/YYYY-MM-DD_sleep.json
  readiness/YYYY/MM/YYYY-MM-DD_readiness.json
  ▲
Apple Shortcut → Apple Health  (manually triggered)
```

A second timer Function (`renew_subscriptions`) runs daily to keep the webhook subscription alive.

## Prerequisites

- Oura Ring with active subscription
- iPhone with Apple Shortcuts
- Azure account (free tier is sufficient)
- Azure CLI (`brew install azure-cli`) + Functions Core Tools (`brew install azure-functions-core-tools@4`)
- [uv](https://docs.astral.sh/uv/) Python package manager (`brew install uv`)
- An [Oura API application](https://cloud.ouraring.com/v2/docs) with client ID and secret

## Setup

### 1. Deploy Azure Infrastructure

```bash
az login
az group create --name oura-hrv-rg --location eastus

az deployment group create \
  --resource-group oura-hrv-rg \
  --template-file infra/main.bicep
```

Note the outputs — you'll need `webhookUrl`, `keyVaultUri`, and `blobBaseUrl`.

### 2. Seed OAuth Tokens into Key Vault

Grant yourself Key Vault access (replace `<your-user-object-id>` — get it with `az ad signed-in-user show --query id -o tsv`):

```bash
az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee <your-user-object-id> \
  --scope /subscriptions/<sub-id>/resourceGroups/oura-hrv-rg/providers/Microsoft.KeyVault/vaults/ourahrv-kv
```

Then run the bootstrap script:

```bash
python scripts/oauth_bootstrap.py \
  --vault-uri https://ourahrv-kv.vault.azure.net/ \
  --client-id <oura-client-id> \
  --client-secret <oura-client-secret>
```

This opens a browser for Oura authorization, then stores the refresh token and client secret in Key Vault.

### 3. Add Client ID to Function App Settings

```bash
az functionapp config appsettings set \
  --name ourahrv-func \
  --resource-group oura-hrv-rg \
  --settings OURA_CLIENT_ID=<your-client-id>
```

### 4. Deploy the Function App

```bash
# Generate requirements.txt from uv (Azure Functions needs pip-format deps)
uv export --no-hashes > requirements.txt

# Deploy
func azure functionapp publish ourahrv-func
```

> **Why `requirements.txt` when we use `uv`?** Azure Functions doesn't understand `pyproject.toml` or `uv.lock`. When you deploy, Azure runs `pip install -r requirements.txt` in the cloud. We generate this file from uv to keep it in sync. See [GETTING_STARTED.md](GETTING_STARTED.md#why-two-requirementstxt-files) for details.

### 5. Register Webhook Subscriptions

The Function must be deployed and reachable first:

```bash
python scripts/create_subscription.py \
  --callback-url https://ourahrv-func.azurewebsites.net/api/webhook \
  --client-id <oura-client-id> \
  --client-secret <oura-client-secret> \
  --verification-token <your-chosen-token>
```

This registers `sleep/create` and `sleep/update` subscriptions. Oura will send a verification challenge to confirm your endpoint works.

Run it a second time with `--data-type daily_readiness` to register the `daily_readiness/create` and `daily_readiness/update` subscriptions (needed for readiness scores):

```bash
python scripts/create_subscription.py \
  --callback-url https://ourahrv-func.azurewebsites.net/api/webhook \
  --client-id <oura-client-id> \
  --client-secret <oura-client-secret> \
  --verification-token <your-chosen-token> \
  --data-type daily_readiness
```

Set the same verification token in your Function App settings:

```bash
az functionapp config appsettings set \
  --name ourahrv-func \
  --resource-group oura-hrv-rg \
  --settings OURA_WEBHOOK_VERIFICATION_TOKEN=<your-chosen-token>
```

### 6. Update Apple Shortcuts

Change the base URL in both Shortcuts from the old GitHub raw URL to:

```
https://ourahrvstore.blob.core.windows.net/oura-data/hrv/{year}/{month}/{date}_sleep-hrv.json
https://ourahrvstore.blob.core.windows.net/oura-data/rhr/{year}/{month}/{date}_sleep-rhr.json
```

The path structure (`YYYY/MM/YYYY-MM-DD_sleep-{metric}.json`) and JSON format are unchanged — only the base URL is different. Everything downstream in the Shortcut (Get Dictionary, Repeat, Log Health Sample) stays the same.

## Health Check

Run the diagnostic script to verify the entire pipeline is working:

```bash
python scripts/check_health.py          # check today + yesterday
python scripts/check_health.py --days 7 # check the last week
```

This checks blob storage for recent data, validates Oura tokens, confirms webhook subscriptions are active, and tests API connectivity. If anything is wrong, it tells you exactly what to fix and how.

Requires `az login` and a `.env` with `OURA_CLIENT_ID`, `KEY_VAULT_URI`, and `BLOB_ACCOUNT_URL`.

For more on reading logs, checking token status, and troubleshooting, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Filling Missing Data

If a webhook was missed (e.g. phone didn't sync in time), you can backfill a single date:

```bash
python scripts/fill_missing.py 2026-08-19
```

The script checks blob storage for both HRV and RHR files. If either is missing, it fetches from the Oura API and uploads only what's absent. If both already exist, it reports that and stops.

Use `--local` to write to `test_output/` instead of blob storage.

Requires `az login` and a `.env` with `OURA_CLIENT_ID` and `KEY_VAULT_URI` (credentials are pulled from Key Vault each run).

For bulk backfills across a date range, use `scripts/backfill.py` instead. It writes sleep and readiness files by default; add `--metrics hrv,rhr` to rewrite HRV/RHR too:

```bash
python scripts/backfill.py --start 2025-01-01 --end 2026-10-10
```

## Re-authorization

Oura refresh tokens eventually expire. When this happens, the health check will report `invalid_grant` — or you can check the Function logs (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md#how-to-check-function-logs)). Run the bootstrap script again:

```bash
python scripts/oauth_bootstrap.py \
  --vault-uri https://ourahrv-kv.vault.azure.net/ \
  --client-id <oura-client-id> \
  --client-secret <oura-client-secret>
```

See [TROUBLESHOOTING.md — Token Expiry](TROUBLESHOOTING.md#oura-token-expiry) for more details on how to tell when this happens and what's going on under the hood.

## Data Format

### HRV (`hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json`)

Bare JSON array, 80-96 entries per night (one per 5-min interval):

```json
[
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
]
```

### RHR (`rhr/YYYY/MM/YYYY-MM-DD_sleep-rhr.json`)

Bare JSON array, typically one entry:

```json
[
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
]
```

### Sleep (`sleep/YYYY/MM/YYYY-MM-DD_sleep.json`)

Bare JSON array, one entry per night. Only overnight sleep (Oura's `long_sleep`) is saved — naps are skipped:

```json
[
  {
    "day": "2026-10-10",
    "bedtime_start": 1760069520,
    "bedtime_start_readable": "2026-10-09 23:12:00",
    "bedtime_end": 1760097600,
    "bedtime_end_readable": "2026-10-10 07:00:00",
    "timezone_offset": "-0500",
    "total_sleep_seconds": 25200,
    "total_sleep_hours": 7.0,
    "time_in_bed_seconds": 28080,
    "source": "oura_ring_sleep",
    "measurement_type": "long_sleep"
  }
]
```

`bedtime_start`/`bedtime_end` are unix timestamps; the `_readable` versions are local time where you slept. `total_sleep_seconds` is actual sleep (excludes awake time in bed).

### Readiness (`readiness/YYYY/MM/YYYY-MM-DD_readiness.json`)

Bare JSON array, one entry per day:

```json
[
  {
    "day": "2026-10-10",
    "readiness": 82,
    "source": "oura_ring_readiness",
    "measurement_type": "readiness_score"
  }
]
```

Timezone fields are per-record and come straight from Oura — they vary by night (home vs. travel, DST changes).

## Cost

Single-user, ~once-daily traffic: **effectively free** on Azure Consumption plan. The Functions free grant (1M executions + 400k GB-s/month) covers this many times over. Blob Storage and Key Vault add cents. Application Insights has a daily cap configured to prevent surprise costs.

## Local Development

```bash
uv sync --extra dev

# Run tests
uv run python -m pytest tests/ -v

# Local Function testing (requires Azurite or a real storage connection)
cp local.settings.json.example local.settings.json
# Edit local.settings.json with your values
func start
```

## Further Reading

- [GETTING_STARTED.md](GETTING_STARTED.md) — Detailed walkthrough of every component, Azure Functions concepts, authentication flows, and how all the pieces fit together
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Health checks, reading function logs, Oura token expiry, and troubleshooting
- [gameplan.md](gameplan.md) — Original design document with decision rationale
