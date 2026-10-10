#!/usr/bin/env python3
"""Fill missing Oura data (HRV, RHR, sleep, readiness) for a single date.

Checks Azure Blob Storage for existing files. For any that are missing,
fetches from the Oura API and uploads only what's absent. Only overnight
sleep ("long_sleep") is used — naps are skipped.

Usage:
    python scripts/fill_missing.py 2026-08-19
    python scripts/fill_missing.py 2026-08-19 --local   # write to test_output/ instead

Requires:
    - az login (uses Key Vault for Oura credentials, same as production)
    - KEY_VAULT_URI and OURA_CLIENT_ID env vars (or .env)
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data_processor import process_readiness_document
from core.oura_client import refresh_access_token
from core.blob_store import blob_path
from backfill import ALL_METRICS, SLEEP_METRICS, build_sleep_files, fetch_all, pick_overnight_by_day

LABELS = {"hrv": "HRV", "rhr": "RHR", "sleep": "Sleep", "readiness": "Readiness"}


def blob_exists(container_client, name):
    blob = container_client.get_blob_client(name)
    try:
        blob.get_blob_properties()
        return True
    except Exception:
        return False


def build_fills(day, missing, sessions, readiness_docs):
    """Work out the file contents for each missing metric on `day`.

    Returns [(metric, bytes or None)]. None means Oura has no valid data
    for that metric on that day (e.g. only a nap, or no readiness score).
    """
    fills = []

    sleep_missing = [m for m in missing if m in SLEEP_METRICS]
    if sleep_missing:
        night = pick_overnight_by_day(sessions).get(day)
        if night:
            fills += [(m, data) for m, _, data in build_sleep_files(night, sleep_missing)]
        else:
            fills += [(m, None) for m in sleep_missing]

    if "readiness" in missing:
        doc = next((d for d in readiness_docs if d.get("day") == day), None)
        data = process_readiness_document(doc)[0] if doc else None
        fills.append(("readiness", data))

    return fills


def main():
    parser = argparse.ArgumentParser(description="Fill missing Oura data for a single date")
    parser.add_argument("date", help="Date to check/fill (YYYY-MM-DD)")
    parser.add_argument("--local", action="store_true", help="Write to test_output/ instead of blob storage")
    args = parser.parse_args()

    try:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        print(f"Invalid date format: {args.date} (expected YYYY-MM-DD)")
        return

    load_dotenv()
    for var in ("OURA_CLIENT_ID", "KEY_VAULT_URI"):
        if not os.getenv(var):
            print(f"Missing {var} — set it in .env or environment")
            return

    container_client = None
    output_dir = None

    if args.local:
        output_dir = Path(__file__).resolve().parent.parent / "test_output"
        exists = lambda name: (output_dir / name).exists()  # noqa: E731
    else:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        account_url = os.environ.get("BLOB_ACCOUNT_URL", "https://ourahrvstore.blob.core.windows.net")
        container_name = os.environ.get("BLOB_CONTAINER_NAME", "oura-data")
        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        container_client = service.get_container_client(container_name)
        exists = lambda name: blob_exists(container_client, name)  # noqa: E731
        print(f"Checking blob storage for {args.date}...")

    missing = []
    for metric in ALL_METRICS:
        name = blob_path(metric, args.date)
        present = exists(name)
        print(f"  {LABELS[metric]:<10} ({name}): {'present' if present else 'MISSING'}")
        if not present:
            missing.append(metric)

    if not missing:
        print("\nAll files already exist. Nothing to do.")
        return

    print("\nRefreshing access token via Key Vault...")
    access_token = refresh_access_token()

    print(f"Fetching Oura data for {args.date}...")
    sessions = fetch_all(access_token, "sleep", day, day) if any(m in SLEEP_METRICS for m in missing) else []
    readiness_docs = fetch_all(access_token, "daily_readiness", day, day) if "readiness" in missing else []

    uploaded = []
    for metric, data in build_fills(args.date, missing, sessions, readiness_docs):
        name = blob_path(metric, args.date)
        if not data:
            print(f"  {LABELS[metric]}: no valid data from Oura for this day (no overnight sleep / no score)")
        elif args.local:
            p = output_dir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            uploaded.append(f"{LABELS[metric]} -> {p}")
        else:
            from azure.storage.blob import ContentSettings
            container_client.upload_blob(
                name, data, overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
            uploaded.append(f"{LABELS[metric]} -> {name}")

    if uploaded:
        print("\nUploaded:")
        for item in uploaded:
            print(f"  {item}")
    else:
        print("\nNo data to upload.")


if __name__ == "__main__":
    main()
