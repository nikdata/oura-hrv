# Getting Started — How This Project Works

A plain-language guide to every piece of this project, written for someone who knows what Azure services are but hasn't built an Azure Functions app before.

---

## Table of Contents

- [The Big Picture](#the-big-picture)
- [What Problem Does This Solve?](#what-problem-does-this-solve)
- [How Data Flows Through the System](#how-data-flows-through-the-system)
- [Project Structure — What Every File Does](#project-structure--what-every-file-does)
- [Azure Functions — The Basics](#azure-functions--the-basics)
- [Why Two `requirements.txt` Files?](#why-two-requirementstxt-files)
- [Azure Concepts Used Here](#azure-concepts-used-here)
- [Security — How Authentication Works](#security--how-authentication-works)
- [The Infrastructure (Bicep)](#the-infrastructure-bicep)
- [Scripts — What Each One Does](#scripts--what-each-one-does)
- [Local Development](#local-development)
- [Common Tasks](#common-tasks)
- [Troubleshooting](#troubleshooting)

---

## The Big Picture

This project takes HRV (Heart Rate Variability) and RHR (Resting Heart Rate) data from your Oura Ring and makes it available for your Apple Shortcuts to import into Apple Health.

The flow is:

```
You wear your Oura Ring → Sleep → Wake up → Open Oura app (ring syncs)
    → Oura sends a webhook to our Azure Function (~30 seconds later)
    → Azure Function fetches the full sleep data from Oura
    → Extracts HRV + RHR → Saves as JSON files in Azure Blob Storage
    → You run your Apple Shortcut → It reads the JSON → Logs to Apple Health
```

That's it. The whole project exists to get data from point A (Oura) to point B (Apple Health) automatically and reliably.

---

## What Problem Does This Solve?

Previously, this pipeline ran on **GitHub Actions** — a cron job that ran 7 times a day, polling the Oura API for new data. The problem was that GitHub Actions cron jobs are unreliable: they get delayed, skipped, or just silently fail. This meant manual reruns and missed data.

The fix: instead of polling ("hey Oura, got any new data?"), we let Oura **push** to us ("hey, new sleep data just arrived"). This is what a webhook does — Oura calls our URL the moment new data appears, typically within 30 seconds of your ring syncing.

---

## How Data Flows Through the System

Let's trace a single night of sleep data, step by step:

### Step 1: Ring syncs
You open the Oura app on your phone. The ring syncs its data to Oura's cloud.

### Step 2: Oura sends a webhook
About 30 seconds later, Oura sends an HTTP POST to our Azure Function URL. The POST body is small — it just says "hey, sleep document XYZ was created/updated." It does NOT contain the actual sleep data.

### Step 3: Function verifies the request
Our `webhook_receiver` function checks the HMAC signature to verify this request really came from Oura (and not someone random poking our URL).

### Step 4: Function fetches the full sleep document
Using the document ID from the webhook, we call back to the Oura API (`GET /v2/usercollection/sleep/{id}`) to get the full sleep document. This document contains the HRV time series, heart rate data, sleep stages, etc.

### Step 5: Extract HRV + RHR
The `data_processor.py` module extracts:
- **HRV**: A time series of ~80-96 readings (one every 5 minutes during sleep), each in rMSSD milliseconds
- **RHR**: The single lowest heart rate during sleep, with the actual timestamp when it occurred

### Step 6: Upload to Blob Storage
The processed JSON files are uploaded to Azure Blob Storage at dated paths:
- `hrv/2026/08/2026-08-21_sleep-hrv.json`
- `rhr/2026/08/2026-08-21_sleep-rhr.json`

### Step 7: Apple Shortcut reads the data
When you run your Apple Shortcut, it fetches these JSON files over plain HTTPS (the blob container is public-read) and logs each reading into Apple Health.

---

## Project Structure — What Every File Does

```
oura-hrv/
│
├── function_app.py              # Entry point — registers all Azure Functions
│
├── functions/                   # Azure Functions (the things that actually run)
│   ├── webhook_receiver.py      # Receives Oura webhooks, fetches data, saves to blob
│   └── renew_subscriptions.py   # Daily timer that keeps webhook subscriptions alive
│
├── core/                        # Shared business logic (used by functions + scripts)
│   ├── data_processor.py        # Extracts HRV + RHR from raw Oura sleep documents
│   ├── oura_client.py           # Oura API calls + OAuth2 token management
│   ├── blob_store.py            # Uploads JSON files to Azure Blob Storage
│   └── security.py              # HMAC signature verification
│
├── scripts/                     # One-off or utility scripts you run locally
│   ├── oauth_bootstrap.py       # First-time OAuth setup (stores tokens in Key Vault)
│   ├── create_subscription.py   # Registers webhook subscriptions with Oura
│   ├── fill_missing.py          # Backfills a single missing date
│   ├── backfill.py              # Backfills a date range
│   ├── test_local.py            # End-to-end test without Azure
│   └── get_tokens_manually.py   # Legacy manual token script (kept for reference)
│
├── tests/                       # Automated tests
│   └── test_data_processor.py   # Verifies HRV/RHR output matches expected shape
│
├── infra/
│   └── main.bicep               # Infrastructure-as-code for all Azure resources
│
├── example/                     # Reference JSON files showing expected output shape
│   ├── 2026-06-01_sleep-hrv.json
│   └── 2026-06-01_sleep-rhr.json
│
├── host.json                    # Azure Functions host configuration
├── local.settings.json.example  # Template for local development settings
├── pyproject.toml               # Python project config (dependencies via uv)
├── uv.lock                      # Locked dependency versions
├── requirements.txt             # Dependencies for Azure deployment (generated from uv)
├── README.md                    # Project overview and setup instructions
├── GETTING_STARTED.md           # This file
└── gameplan.md                  # Original design document / decision log
```

---

## Azure Functions — The Basics

### What is an Azure Function?

An Azure Function is a small piece of code that runs in response to an event. You don't manage a server — Azure runs your code when it's triggered and shuts it down when it's done. You pay only for the time your code actually runs (and at our scale, it's free).

### How is it structured?

Azure Functions v2 for Python uses this pattern:

1. **`function_app.py`** — The entry point. Azure looks for this file when your app starts. It creates a `FunctionApp` and registers all your functions.

2. **Blueprints** — Each function is defined in its own file using a `Blueprint`. A Blueprint is just a way to organize functions across multiple files (similar to Flask blueprints if you've used Flask). The Blueprint defines what triggers the function (HTTP request, timer, etc.) and what code runs.

3. **Triggers** — Each function has exactly one trigger:
   - `webhook_receiver` has an **HTTP trigger** (`@bp.route(...)`) — it runs when someone sends an HTTP request to its URL
   - `renew_subscriptions` has a **timer trigger** (`@bp.timer_trigger(...)`) — it runs on a cron schedule

4. **`host.json`** — Configuration for the Functions runtime itself (logging, extensions). You rarely need to change this.

### What happens when the Function App starts?

1. Azure loads `function_app.py`
2. It sees the registered blueprints and discovers two functions
3. It sets up the triggers:
   - Creates an HTTP endpoint at `/api/webhook` for the webhook receiver
   - Schedules a timer for `0 0 12 * * *` (daily at noon UTC) for the subscription renewal
4. When a trigger fires, Azure runs the corresponding function

### Where does it run?

On Azure's **Consumption plan** — the cheapest option. Azure allocates a container to run your code when triggered, and deallocates it when idle. This means:
- **Cold starts**: The first request after a period of inactivity takes a few extra seconds (Azure needs to spin up a container). This is fine for webhooks — Oura is not waiting for a fast response.
- **Cost**: Effectively free. The free tier includes 1 million executions and 400,000 GB-seconds per month. We use maybe 60 executions/month.

### The auth_level thing

```python
@bp.route(route="webhook", methods=["POST", "GET"], auth_level=func.AuthLevel.ANONYMOUS)
```

`auth_level=ANONYMOUS` means anyone can call this URL without an Azure function key. This sounds scary but is intentional — Oura can't send function keys with its webhooks. Our security comes from HMAC signature verification instead (see the Security section below).

---

## Why Two `requirements.txt` Files?

This is a common point of confusion. Here's why both exist:

### `pyproject.toml` + `uv.lock` — For local development

This project uses **uv** as its Python package manager (similar to pip but faster, with lockfile support). `pyproject.toml` lists the project's dependencies, and `uv.lock` pins exact versions. When you run `uv sync`, it installs everything from the lockfile.

### `requirements.txt` — For Azure deployment

Azure Functions **does not understand `pyproject.toml` or uv**. When you deploy (`func azure functionapp publish ...`), Azure's build system looks for a `requirements.txt` and runs `pip install -r requirements.txt` to install dependencies in the cloud.

So we generate `requirements.txt` from uv before deploying:

```bash
uv export --no-hashes > requirements.txt
```

The `--no-hashes` flag removes hash checksums that Azure's pip version sometimes chokes on.

**Important:** `requirements.txt` is a subset of `pyproject.toml` — it excludes dev dependencies (like pytest) that aren't needed in production. The version ranges in `requirements.txt` should match what's in `pyproject.toml`.

### In short:

| File | Used by | When |
|------|---------|------|
| `pyproject.toml` + `uv.lock` | uv | Local development (`uv sync`, `uv run`) |
| `requirements.txt` | Azure Functions | Deployment (`func azure functionapp publish`) |

---

## Azure Concepts Used Here

### Managed Identity

Instead of storing Azure credentials (like storage keys or Key Vault passwords) in your code, the Function App has a **managed identity**. Think of it as Azure saying "this Function App is allowed to access these other Azure resources" — no passwords involved.

In the code, `DefaultAzureCredential()` picks up this identity automatically:
- **In Azure**: Uses the Function App's managed identity
- **Locally**: Uses your `az login` session

The Bicep template sets up the managed identity and grants it access to Key Vault and Blob Storage.

### Key Vault

A secure place to store secrets (like API keys and tokens). In this project, Key Vault holds:
- `oura-refresh-token` — The OAuth refresh token for the Oura API
- `oura-client-secret` — The Oura application's client secret

Why Key Vault instead of just putting these in the Function App's environment variables? Because the refresh token **rotates** — Oura may return a new one each time we use it, and we need to save the new one. Writing back to Key Vault is a clean, supported operation. Writing back to app settings would require the Azure management API, which is hacky.

### Blob Storage

Azure's object storage service (like AWS S3). We use one storage account for two purposes:
1. **Azure Functions internal storage** (`AzureWebJobsStorage`) — Azure Functions needs a storage account for its own bookkeeping (timer state, function execution logs, etc.)
2. **Our data** (`oura-data` container) — Where the HRV/RHR JSON files live

The `oura-data` container has **public blob-level read access**, meaning anyone with the URL can download a file. This is the same trust model as the old public GitHub repo — the Apple Shortcut needs unauthenticated access.

### Application Insights

Azure's monitoring/logging service. When your Function runs, logs go to Application Insights. You can view them in the Azure Portal under the Function App's "Monitor" section, or in the Application Insights resource directly.

**Cost warning**: Application Insights charges by data volume. For a project this small, the cost is negligible, but the Bicep template enables sampling to prevent surprises.

---

## Security — How Authentication Works

There are three separate authentication mechanisms in this project. They serve different purposes and should not be confused:

### 1. OAuth2 Bearer Token (for Oura data API)

Used when: Fetching sleep documents from `api.ouraring.com/v2/usercollection/sleep/...`

How it works:
- We store a refresh token in Key Vault
- Before each API call, we exchange the refresh token for a short-lived access token
- We send the access token as `Authorization: Bearer <token>` header
- If Oura returns a new refresh token, we save it back to Key Vault

This is the standard OAuth2 refresh-token flow. The access token expires quickly (typically ~24 hours), but the refresh token lasts longer and can be reused.

### 2. Client Credentials (for Oura webhook management)

Used when: Creating, listing, or renewing webhook subscriptions

How it works:
- Sent as `x-client-id` and `x-client-secret` headers
- These are your Oura *application's* credentials (from the Oura developer portal)
- They don't expire or rotate

This is NOT the same as the OAuth bearer token. These headers authenticate your *app*, not your *user account*.

### 3. HMAC Signature (for incoming webhook verification)

Used when: Receiving webhook notifications from Oura

How it works:
- Oura computes `HMAC-SHA256(client_secret, timestamp + request_body)`, hex-encodes it, and uppercases it
- Sends it as the `x-oura-signature` header, along with `x-oura-timestamp`
- Our function repeats the computation and compares the result
- If they match, the request is authentic

This prevents someone from finding our webhook URL and sending fake data. Without knowing the client secret, they can't forge a valid signature.

---

## The Infrastructure (Bicep)

`infra/main.bicep` defines all Azure resources as code. Bicep is Azure's infrastructure-as-code language (like Terraform, but Azure-native). When you run `az deployment group create --template-file infra/main.bicep`, Azure creates or updates all these resources:

| Resource | What it is | Why we need it |
|----------|-----------|---------------|
| **Storage Account** | Blob storage + Functions internal storage | Store HRV/RHR JSON files; Functions runtime needs storage for bookkeeping |
| **Blob Container** (`oura-data`) | A folder within the storage account | Holds the JSON files, configured for public read access |
| **Function App** | The compute that runs our code | Hosts and executes our two functions |
| **Consumption Plan** | The pricing/compute tier | Pay-per-execution (effectively free at our scale) |
| **Key Vault** | Secrets store | Holds the Oura refresh token and client secret |
| **Application Insights** | Logging/monitoring | See function execution logs and errors |
| **Role Assignments** | IAM permissions | Grant the Function App access to Key Vault (read+write secrets) and Blob Storage (read+write blobs) |

The Bicep template also configures app settings on the Function App (environment variables it needs at runtime):

| Setting | Value | Purpose |
|---------|-------|---------|
| `KEY_VAULT_URI` | `https://ourahrv-kv.vault.azure.net/` | Where to find secrets |
| `BLOB_ACCOUNT_URL` | `https://ourahrvstore.blob.core.windows.net` | Where to upload JSON files |
| `BLOB_CONTAINER_NAME` | `oura-data` | Which container to use |
| `OURA_CLIENT_ID` | (set manually after deploy) | Oura app client ID |
| `AzureWebJobsStorage` | (connection string) | Functions internal storage |
| `FUNCTIONS_WORKER_RUNTIME` | `python` | Tells Azure this is a Python app |
| `FUNCTIONS_EXTENSION_VERSION` | `~4` | Which version of the Functions runtime |
| `APPINSIGHTS_INSTRUMENTATIONKEY` | (auto-generated) | Links to Application Insights |

---

## Scripts — What Each One Does

All scripts in `scripts/` are run locally on your machine (not in Azure). They help with setup, maintenance, and debugging.

### `oauth_bootstrap.py` — First-time setup + re-authorization

**When to run:** Once during initial setup, and again whenever the Oura token chain dies (you'll see `invalid_grant` in the Function logs).

**What it does:**
1. Opens your browser to Oura's authorization page
2. You log in and grant access
3. Starts a tiny local web server to catch the OAuth callback
4. Exchanges the authorization code for tokens
5. Stores the refresh token and client secret in Azure Key Vault

**Requires:** `az login` (so it can write to Key Vault)

### `create_subscription.py` — Register webhook subscriptions

**When to run:** Once during initial setup, after the Function App is deployed and reachable.

**What it does:**
1. Registers two webhook subscriptions with Oura:
   - `sleep/create` — fires when a new sleep document appears
   - `sleep/update` — fires when Oura revises an existing sleep document
2. Oura sends a verification challenge to your Function URL to confirm you own it

**Important:** The Function must be deployed first. Oura calls your URL during registration.

### `fill_missing.py` — Backfill one missing date

**When to run:** When a webhook was missed for a specific date (phone didn't sync in time, Function was down, etc.)

**What it does:**
1. Checks Blob Storage for existing HRV/RHR files for that date
2. If either is missing, fetches from the Oura API and uploads
3. If both exist, reports that and stops

Use `--local` to write to `test_output/` instead of Blob Storage (good for testing).

### `backfill.py` — Backfill a date range

**When to run:** For bulk backfills (e.g., filling in all historical data after first deployment).

**What it does:** Same as `fill_missing.py` but for a range of dates. Fetches in 30-day chunks to stay within Oura's API limits.

**Note:** This script uses `.env` credentials directly (not Key Vault), because it was designed for one-time bulk operations where Key Vault round-trips would be slow.

### `test_local.py` — End-to-end test without Azure

**When to run:** When you want to verify the data processing pipeline works, without deploying to Azure.

**What it does:**
1. Refreshes an Oura access token using `.env` credentials
2. Fetches recent sleep data (last 3 days)
3. Runs each session through the data processor
4. Writes output to `test_output/` for inspection

### `get_tokens_manually.py` — Legacy token script

A leftover from the old pipeline. Kept for reference. Use `oauth_bootstrap.py` for new token operations.

---

## Local Development

### Prerequisites

```bash
# Install uv (Python package manager)
brew install uv

# Install Azure Functions Core Tools (for running functions locally)
brew install azure-functions-core-tools@4

# Install Azure CLI (for deploying and managing resources)
brew install azure-cli
```

### Setting up the project

```bash
# Clone the repo and install dependencies (including dev tools like pytest)
uv sync --extra dev

# Copy the example settings file
cp local.settings.json.example local.settings.json
# Edit local.settings.json with your real values
```

### Running tests

```bash
uv run python -m pytest tests/ -v
```

### Running functions locally

```bash
# Requires local.settings.json to be configured
# Requires Azurite (local storage emulator) or a real storage connection
func start
```

This starts the Functions runtime locally. The webhook receiver will be available at `http://localhost:7071/api/webhook`.

### Testing the data pipeline without Azure

```bash
# Create a .env file with your Oura credentials
echo "OURA_CLIENT_ID=your-id" >> .env
echo "OURA_CLIENT_SECRET=your-secret" >> .env
echo "OURA_REFRESH_TOKEN=your-token" >> .env

# Run the local test script
python scripts/test_local.py
```

This fetches real data from Oura and writes processed JSON to `test_output/`.

---

## Common Tasks

### Deploying to Azure

```bash
# 1. Generate requirements.txt from uv (Azure needs this for pip install)
uv export --no-hashes > requirements.txt

# 2. Deploy the Function App
func azure functionapp publish ourahrv-func
```

### Checking if webhooks are working

Look at the Function App logs in the Azure Portal:
1. Go to your Function App → Monitor → Invocations
2. Or go to Application Insights → Logs and run:
   ```
   traces | where message contains "Processing sleep" | order by timestamp desc
   ```

### Re-authorizing after token expiry

When you see `invalid_grant` in the logs:

```bash
python scripts/oauth_bootstrap.py \
  --vault-uri https://ourahrv-kv.vault.azure.net/ \
  --client-id <your-client-id> \
  --client-secret <your-client-secret>
```

### Backfilling a missed date

```bash
python scripts/fill_missing.py 2026-08-19
```

### Checking webhook subscription status

```bash
# From the Azure Portal → Function App → Monitor
# Look for "Renewed subscription" or "No webhook subscriptions found" in logs
```

---

## Troubleshooting

### "No webhook subscriptions found"

The daily renewal timer didn't find any subscriptions. They may have expired. Re-run `scripts/create_subscription.py`.

### "HMAC verification failed"

Either:
- Someone is hitting your webhook URL with fake requests (normal, ignore)
- The client secret in Key Vault doesn't match the one registered with Oura (re-run `oauth_bootstrap.py`)

### "invalid_grant" in logs

The Oura refresh token has expired or been revoked. Re-run `oauth_bootstrap.py` to get a fresh one.

### "No valid HRV data for {date}"

Oura's sleep document for that date didn't contain usable HRV data. This can happen with very short sleep sessions or naps. Usually resolves with the next `sleep/update` event.

### Cold start delays

The first webhook after a period of inactivity may take a few extra seconds (Azure is spinning up a container). This is normal and expected on the Consumption plan. Oura's webhook delivery is async and tolerates this delay.

### Apple Shortcut not finding data

1. Check that the blob exists: visit `https://ourahrvstore.blob.core.windows.net/oura-data/hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json` in a browser
2. If it doesn't exist, the webhook may not have fired — check Function logs or run `fill_missing.py`
3. If it exists but the Shortcut fails, verify the base URL in the Shortcut matches the blob URL
