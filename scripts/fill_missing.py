#!/usr/bin/env python3
"""Fill missing HRV/RHR data for a single date.

Checks Azure Blob Storage for existing data. If either HRV or RHR is
missing, fetches from the Oura API and uploads only what's absent.

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
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data_processor import process_sleep_document
from core.oura_client import refresh_access_token, API_BASE
from core.blob_store import blob_path


def fetch_sleep_for_date(access_token, date_str):
    url = f"{API_BASE}/usercollection/sleep?start_date={date_str}&end_date={date_str}"
    response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", "60"))
        print(f"  Rate limited, waiting {retry_after}s...")
        time.sleep(retry_after)
        response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    response.raise_for_status()
    return response.json().get("data", [])


def blob_exists(container_client, name):
    blob = container_client.get_blob_client(name)
    try:
        blob.get_blob_properties()
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Fill missing Oura HRV/RHR data for a single date")
    parser.add_argument("date", help="Date to check/fill (YYYY-MM-DD)")
    parser.add_argument("--local", action="store_true", help="Write to test_output/ instead of blob storage")
    args = parser.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"Invalid date format: {args.date} (expected YYYY-MM-DD)")
        return

    load_dotenv()
    for var in ("OURA_CLIENT_ID", "KEY_VAULT_URI"):
        if not os.getenv(var):
            print(f"Missing {var} — set it in .env or environment")
            return

    hrv_name = blob_path("hrv", args.date)
    rhr_name = blob_path("rhr", args.date)

    container_client = None
    output_dir = None
    hrv_missing = True
    rhr_missing = True

    if args.local:
        output_dir = Path(__file__).resolve().parent.parent / "test_output"
        hrv_missing = not (output_dir / hrv_name).exists()
        rhr_missing = not (output_dir / rhr_name).exists()
    else:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        account_url = os.environ.get("BLOB_ACCOUNT_URL", "https://ourahrvstore.blob.core.windows.net")
        container_name = os.environ.get("BLOB_CONTAINER_NAME", "oura-data")
        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        container_client = service.get_container_client(container_name)

        print(f"Checking blob storage for {args.date}...")
        hrv_missing = not blob_exists(container_client, hrv_name)
        rhr_missing = not blob_exists(container_client, rhr_name)

    print(f"  HRV ({hrv_name}): {'MISSING' if hrv_missing else 'present'}")
    print(f"  RHR ({rhr_name}): {'MISSING' if rhr_missing else 'present'}")

    if not hrv_missing and not rhr_missing:
        print("\nBoth files already exist. Nothing to do.")
        return

    print("\nRefreshing access token via Key Vault...")
    access_token = refresh_access_token()

    print(f"Fetching sleep data for {args.date}...")
    sessions = fetch_sleep_for_date(access_token, args.date)

    if not sessions:
        print(f"No sleep sessions found for {args.date}.")
        return

    session = sessions[-1]
    hrv_bytes, rhr_bytes, sleep_date = process_sleep_document(session)

    uploaded = []

    if hrv_missing:
        if not hrv_bytes:
            print("  HRV: no valid data in sleep session")
        elif args.local:
            p = output_dir / hrv_name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(hrv_bytes)
            uploaded.append(f"HRV -> {p}")
        else:
            from azure.storage.blob import ContentSettings
            container_client.upload_blob(
                hrv_name, hrv_bytes, overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
            uploaded.append(f"HRV -> {hrv_name}")

    if rhr_missing:
        if not rhr_bytes:
            print("  RHR: no valid data in sleep session")
        elif args.local:
            p = output_dir / rhr_name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(rhr_bytes)
            uploaded.append(f"RHR -> {p}")
        else:
            from azure.storage.blob import ContentSettings
            container_client.upload_blob(
                rhr_name, rhr_bytes, overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
            uploaded.append(f"RHR -> {rhr_name}")

    if uploaded:
        print("\nUploaded:")
        for item in uploaded:
            print(f"  {item}")
    else:
        print("\nNo data to upload.")


if __name__ == "__main__":
    main()
