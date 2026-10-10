#!/usr/bin/env python3
"""Pipeline health check — run this to see if everything is working.

Checks blob storage for recent data (HRV, RHR, sleep, readiness), validates Oura tokens, and
confirms webhook subscriptions are active. Prints a clear summary
with fix instructions for any problems found.

Usage:
    python scripts/check_health.py          # check today + yesterday
    python scripts/check_health.py --days 7 # check the last 7 days

Requires:
    - az login (uses your Azure session for blob/Key Vault access)
    - .env with OURA_CLIENT_ID, KEY_VAULT_URI, BLOB_ACCOUNT_URL
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.blob_store import blob_path


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg):
    print(f"  {GREEN}OK{RESET}  {msg}")


def fail(msg):
    print(f"  {RED}FAIL{RESET}  {msg}")


def warn(msg):
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def heading(msg):
    print(f"\n{BOLD}{msg}{RESET}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_env_vars():
    """Verify required environment variables are set."""
    heading("1. Environment variables")
    required = {
        "OURA_CLIENT_ID": "Oura app client ID (from developer portal)",
        "KEY_VAULT_URI": "Key Vault URL (e.g. https://ourahrv-kv.vault.azure.net/)",
    }
    optional = {
        "BLOB_ACCOUNT_URL": ("Blob storage URL", "https://ourahrvstore.blob.core.windows.net"),
        "BLOB_CONTAINER_NAME": ("Blob container name", "oura-data"),
    }

    all_good = True
    for var, desc in required.items():
        if os.getenv(var):
            ok(f"{var} is set")
        else:
            fail(f"{var} is missing — {desc}")
            all_good = False

    for var, (desc, default) in optional.items():
        if os.getenv(var):
            ok(f"{var} is set")
        else:
            ok(f"{var} not set — will use default: {default}")

    if not all_good:
        print(f"\n  {BOLD}Fix:{RESET} Add missing variables to your .env file.")
    return all_good


METRICS = ("hrv", "rhr", "sleep", "readiness")
LABELS = {"hrv": "HRV", "rhr": "RHR", "sleep": "Sleep", "readiness": "Readiness"}

# Webhook subscriptions the pipeline needs, with what breaks if one is missing
REQUIRED_SUBSCRIPTIONS = {
    ("sleep", "create"): "new sleep data won't trigger the pipeline",
    ("sleep", "update"): "revised sleep data won't be captured",
    ("daily_readiness", "create"): "new readiness scores won't be captured",
    ("daily_readiness", "update"): "revised readiness scores won't be captured",
}


def check_blob_storage(days):
    """Check if HRV/RHR/sleep/readiness files exist in blob storage for recent dates."""
    heading("2. Blob storage (recent data)")

    account_url = os.environ.get("BLOB_ACCOUNT_URL", "https://ourahrvstore.blob.core.windows.net")
    container_name = os.environ.get("BLOB_CONTAINER_NAME", "oura-data")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        container = service.get_container_client(container_name)
        # Quick connectivity check
        container.get_container_properties()
    except Exception as e:
        fail(f"Cannot connect to blob storage: {e}")
        print(f"\n  {BOLD}Fix:{RESET} Run 'az login' and verify BLOB_ACCOUNT_URL in .env.")
        return False

    today = datetime.now().date()
    missing_by_date = {}

    for i in range(days):
        date_str = (today - timedelta(days=i)).isoformat()
        present = {m: _blob_exists(container, blob_path(m, date_str)) for m in METRICS}
        have = [LABELS[m] for m in METRICS if present[m]]
        missing = [m for m in METRICS if not present[m]]

        if not missing:
            # Get the last modified time so you can see when it was written
            hrv_props = container.get_blob_client(blob_path("hrv", date_str)).get_blob_properties()
            modified = hrv_props.last_modified.strftime("%Y-%m-%d %H:%M UTC")
            ok(f"{date_str}: {' + '.join(have)} present (last updated {modified})")
        elif have:
            warn(f"{date_str}: {', '.join(have)} present; missing {', '.join(LABELS[m] for m in missing)}")
            missing_by_date[date_str] = missing
        else:
            fail(f"{date_str}: no data")
            missing_by_date[date_str] = missing

    if missing_by_date:
        print(f"\n  {BOLD}Fix:{RESET} Backfill missing dates (skip any day you didn't wear the ring):")
        for cmd in fix_commands(missing_by_date):
            print(f"    {cmd}")

    return not missing_by_date


def fix_commands(missing_by_date):
    """Commands to backfill each date's missing files.

    fill_missing.py handles HRV/RHR; backfill.py handles sleep/readiness.
    """
    cmds = []
    for date_str, missing in missing_by_date.items():
        if "hrv" in missing or "rhr" in missing:
            cmds.append(f"python scripts/fill_missing.py {date_str}")
        other = [m for m in missing if m in ("sleep", "readiness")]
        if other:
            cmds.append(
                f"python scripts/backfill.py --start {date_str} --end {date_str} --metrics {','.join(other)}"
            )
    return cmds


def _blob_exists(container, name):
    try:
        container.get_blob_client(name).get_blob_properties()
        return True
    except Exception:
        return False


def check_oura_token():
    """Check if the Oura OAuth token is still valid."""
    heading("3. Oura API token")

    try:
        from core.oura_client import refresh_access_token
        access_token = refresh_access_token()
        ok(f"Token refresh succeeded (access token: {access_token[:12]}...)")
        return True
    except RuntimeError as e:
        if "invalid_grant" in str(e):
            fail("Refresh token is dead (invalid_grant)")
            print(f"\n  {BOLD}Fix:{RESET} Re-authorize with Oura:")
            print(f"    python scripts/oauth_bootstrap.py \\")
            print(f"      --vault-uri {os.environ.get('KEY_VAULT_URI', 'https://ourahrv-kv.vault.azure.net/')} \\")
            print(f"      --client-id <your-client-id> --client-secret <your-client-secret>")
        else:
            fail(f"Token refresh failed: {e}")
        return False
    except Exception as e:
        fail(f"Cannot reach Key Vault or Oura API: {e}")
        print(f"\n  {BOLD}Fix:{RESET} Run 'az login' and verify KEY_VAULT_URI in .env.")
        return False


def check_webhook_subscriptions():
    """Check if webhook subscriptions are active."""
    heading("4. Webhook subscriptions")

    try:
        from core.oura_client import list_webhook_subscriptions
        subs = list_webhook_subscriptions()
    except Exception as e:
        fail(f"Cannot list subscriptions: {e}")
        print(f"\n  {BOLD}Fix:{RESET} Verify OURA_CLIENT_ID and that oura-client-secret is in Key Vault.")
        return False

    if not subs:
        fail("No webhook subscriptions found")
        print(f"\n  {BOLD}Fix:{RESET} Re-create subscriptions:")
        print(f"    python scripts/create_subscription.py \\")
        print(f"      --callback-url https://ourahrv-func.azurewebsites.net/api/webhook \\")
        print(f"      --client-id <your-client-id> --client-secret <your-client-secret> \\")
        print(f"      --verification-token <your-token>")
        print(f"    Then run it again with --data-type daily_readiness")
        return False

    found = set()
    for sub in subs:
        data_type = sub.get("data_type", "?")
        event_type = sub.get("event_type", "?")
        sub_id = sub.get("id", "?")
        expiration = sub.get("expiration_time", "unknown")

        ok(f"{data_type}/{event_type} (id: {sub_id[:16]}..., expires: {expiration})")
        found.add((data_type, event_type))

    all_good = True
    for (data_type, event_type), consequence in REQUIRED_SUBSCRIPTIONS.items():
        if (data_type, event_type) not in found:
            warn(f"Missing {data_type}/{event_type} subscription — {consequence}")
            all_good = False

    if not all_good:
        print(f"\n  {BOLD}Fix:{RESET} Re-create the missing subscription(s) with scripts/create_subscription.py")
        print("    (add --data-type daily_readiness for the readiness ones)")

    return all_good


def check_oura_api():
    """Quick check that we can actually fetch data from Oura."""
    heading("5. Oura API connectivity")

    try:
        import requests
        from core.oura_client import refresh_access_token, API_BASE

        access_token = refresh_access_token()
        today = datetime.now().date().isoformat()
        url = f"{API_BASE}/usercollection/sleep?start_date={today}&end_date={today}"
        response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})

        if response.status_code == 200:
            data = response.json().get("data", [])
            if data:
                ok(f"Fetched {len(data)} sleep session(s) for today")
            else:
                ok("API is reachable (no sleep data for today yet — normal if you haven't synced)")
        elif response.status_code == 429:
            warn("Rate limited by Oura (try again in a minute)")
        else:
            fail(f"Oura API returned {response.status_code}: {response.text[:100]}")
            return False
        return True
    except Exception as e:
        fail(f"Cannot reach Oura API: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Check if the Oura HRV pipeline is healthy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Runs all checks and tells you what (if anything) needs fixing.",
    )
    parser.add_argument("--days", type=int, default=2,
                        help="How many days of blob storage to check (default: 2)")
    args = parser.parse_args()

    load_dotenv()

    print(f"{BOLD}Oura HRV Pipeline Health Check{RESET}")
    print(f"Checking {args.days} day(s) of data...\n")

    results = {}

    # Run checks in order — some depend on env vars being set
    results["env"] = check_env_vars()

    if results["env"]:
        results["blobs"] = check_blob_storage(args.days)
        results["token"] = check_oura_token()
        results["subscriptions"] = check_webhook_subscriptions()
        results["api"] = check_oura_api()
    else:
        print("\n  Skipping remaining checks until env vars are fixed.")

    # Summary
    heading("Summary")
    all_passed = all(results.values())

    if all_passed:
        print(f"  {GREEN}{BOLD}All checks passed.{RESET} Pipeline is healthy.\n")
    else:
        failed = [name for name, passed in results.items() if not passed]
        print(f"  {RED}{BOLD}{len(failed)} check(s) need attention.{RESET} See details above.\n")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
