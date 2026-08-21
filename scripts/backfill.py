#!/usr/bin/env python3
"""Backfill HRV/RHR data from Oura API into Azure Blob Storage.

Fetches all sleep data between two dates, processes each session,
and uploads to blob storage using the same path structure as the
webhook pipeline.

Usage:
    python scripts/backfill.py --start 2025-01-01 --end 2026-08-19

Requires:
    - .env with OURA_CLIENT_ID, OURA_CLIENT_SECRET, OURA_REFRESH_TOKEN
    - Azure credentials (az login) or BLOB_ACCOUNT_URL + BLOB_CONTAINER_NAME env vars
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data_processor import process_sleep_document
from core.blob_store import blob_path

TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2"


def refresh_token():
    response = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": os.environ["OURA_REFRESH_TOKEN"],
        "client_id": os.environ["OURA_CLIENT_ID"],
        "client_secret": os.environ["OURA_CLIENT_SECRET"],
    })
    if response.status_code != 200:
        print(f"Token refresh failed: {response.status_code}")
        print(response.text)
        raise SystemExit(1)
    tokens = response.json()

    if "refresh_token" in tokens and tokens["refresh_token"] != os.environ["OURA_REFRESH_TOKEN"]:
        print(f"NOTE: Oura rotated your refresh token. Update .env with:")
        print(f"  OURA_REFRESH_TOKEN={tokens['refresh_token']}")
        os.environ["OURA_REFRESH_TOKEN"] = tokens["refresh_token"]

    return tokens["access_token"]


def fetch_sleep_range(access_token, start_date, end_date):
    """Fetch sleep sessions for a date range. Oura allows up to ~30 days per call."""
    url = f"{API_BASE}/usercollection/sleep?start_date={start_date}&end_date={end_date}"
    response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", "60"))
        print(f"  Rate limited, waiting {retry_after}s...")
        time.sleep(retry_after)
        response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    response.raise_for_status()
    return response.json().get("data", [])


def upload_to_blob(container_client, metric, sleep_date, data_bytes):
    from azure.storage.blob import ContentSettings
    name = blob_path(metric, sleep_date)
    container_client.upload_blob(
        name, data_bytes, overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )
    return name


def main():
    parser = argparse.ArgumentParser(description="Backfill Oura HRV/RHR data to Azure Blob Storage")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--local", action="store_true", help="Write to test_output/ instead of blob storage")
    args = parser.parse_args()

    load_dotenv()
    for var in ("OURA_CLIENT_ID", "OURA_CLIENT_SECRET", "OURA_REFRESH_TOKEN"):
        if not os.getenv(var):
            print(f"Missing {var} in .env")
            return

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    container_client = None
    output_dir = None
    if args.local:
        output_dir = Path(__file__).resolve().parent.parent / "test_output"
        output_dir.mkdir(exist_ok=True)
    else:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        account_url = os.environ.get("BLOB_ACCOUNT_URL", "https://ourahrvstore.blob.core.windows.net")
        container_name = os.environ.get("BLOB_CONTAINER_NAME", "oura-data")
        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        container_client = service.get_container_client(container_name)

    print("Refreshing access token...")
    access_token = refresh_token()
    print("OK\n")

    # Fetch in 30-day chunks (Oura API limit)
    chunk_size = timedelta(days=30)
    all_sessions = []
    current = start
    while current <= end:
        chunk_end = min(current + chunk_size, end)
        print(f"Fetching {current} to {chunk_end}...")
        sessions = fetch_sleep_range(access_token, current.isoformat(), chunk_end.isoformat())
        print(f"  {len(sessions)} session(s)")
        all_sessions.extend(sessions)
        current = chunk_end + timedelta(days=1)
        if current <= end:
            time.sleep(1)

    print(f"\nTotal: {len(all_sessions)} sleep sessions\n")

    # Deduplicate by day (keep last session per day — most complete)
    by_day = {}
    for session in all_sessions:
        day = session.get("day", "unknown")
        by_day[day] = session
    print(f"Unique days: {len(by_day)}\n")

    hrv_count = 0
    rhr_count = 0
    for day in sorted(by_day.keys()):
        session = by_day[day]
        hrv_bytes, rhr_bytes, sleep_date = process_sleep_document(session)

        if args.local:
            if hrv_bytes:
                p = output_dir / blob_path("hrv", sleep_date)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(hrv_bytes)
                hrv_count += 1
            if rhr_bytes:
                p = output_dir / blob_path("rhr", sleep_date)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(rhr_bytes)
                rhr_count += 1
        else:
            if hrv_bytes:
                upload_to_blob(container_client, "hrv", sleep_date, hrv_bytes)
                hrv_count += 1
            if rhr_bytes:
                upload_to_blob(container_client, "rhr", sleep_date, rhr_bytes)
                rhr_count += 1

        print(f"  {day}: HRV={'yes' if hrv_bytes else 'no'} RHR={'yes' if rhr_bytes else 'no'}")

    dest = "test_output/" if args.local else "blob storage"
    print(f"\nDone. Uploaded {hrv_count} HRV + {rhr_count} RHR files to {dest}.")


if __name__ == "__main__":
    main()
