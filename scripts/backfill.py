#!/usr/bin/env python3
"""Backfill Oura data (sleep times, readiness, HRV, RHR) into Azure Blob Storage.

Fetches data between two dates in 30-day chunks, processes it with the
same code as the webhook pipeline, and uploads to the same blob paths.

Only overnight sleep ("long_sleep") is used — naps are skipped so they
never overwrite a night's data. Days with no data are skipped and listed
in the summary at the end; that's expected (e.g. ring not worn).

Usage:
    python scripts/backfill.py --start 2025-01-01 --end 2026-10-10
    python scripts/backfill.py --start 2025-01-01 --end 2026-10-10 --local
    python scripts/backfill.py --start 2025-01-01 --end 2026-10-10 --metrics hrv,rhr

--metrics defaults to "sleep,readiness" so existing HRV/RHR files are
left alone unless you ask for them.

Requires:
    - az login (uses Key Vault for Oura credentials, same as production)
    - KEY_VAULT_URI and OURA_CLIENT_ID env vars (or .env)
    - BLOB_ACCOUNT_URL + BLOB_CONTAINER_NAME env vars (optional, have defaults)
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data_processor import (
    is_overnight_sleep, process_readiness_document, process_sleep_document, process_sleep_times,
)
from core.oura_client import refresh_access_token, API_BASE
from core.blob_store import blob_path

ALL_METRICS = ("hrv", "rhr", "sleep", "readiness")
SLEEP_METRICS = ("hrv", "rhr", "sleep")


def _get(url, access_token, params):
    response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", "60"))
        print(f"  Rate limited, waiting {retry_after}s...")
        time.sleep(retry_after)
        response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
    response.raise_for_status()
    return response.json()


def fetch_range(access_token, collection, start_date, end_date):
    """Fetch all documents for a date range. Oura allows up to ~30 days per call.

    Follows next_token if Oura splits the results into multiple pages.
    """
    url = f"{API_BASE}/usercollection/{collection}"
    params = {"start_date": start_date, "end_date": end_date}
    docs = []
    while True:
        body = _get(url, access_token, params)
        docs.extend(body.get("data", []))
        next_token = body.get("next_token")
        if not next_token:
            return docs
        params = {**params, "next_token": next_token}


def fetch_all(access_token, collection, start, end):
    """Fetch every document between start and end (inclusive), 30 days at a time."""
    chunk_size = timedelta(days=30)
    docs = []
    current = start
    while current <= end:
        chunk_end = min(current + chunk_size, end)
        # Oura's sleep list leaves out the end_date day itself (daily_readiness
        # includes it), so ask for one extra day to get chunk_end's night.
        api_end = chunk_end + timedelta(days=1) if collection == "sleep" else chunk_end
        print(f"  {collection}: {current} to {chunk_end}...", end=" ")
        chunk = fetch_range(access_token, collection, current.isoformat(), api_end.isoformat())
        print(f"{len(chunk)} doc(s)")
        docs.extend(chunk)
        current = chunk_end + timedelta(days=1)
        if current <= end:
            time.sleep(1)
    return docs


def pick_overnight_by_day(sessions):
    """Pick one overnight sleep session per day, skipping naps.

    If Oura split a night into two long_sleep sessions, keep the one with
    the most sleep.
    """
    by_day = {}
    for session in sessions:
        if not is_overnight_sleep(session):
            continue
        day = session["day"]
        current = by_day.get(day)
        if current is None or (session.get("total_sleep_duration") or 0) > (current.get("total_sleep_duration") or 0):
            by_day[day] = session
    return by_day


def build_sleep_files(session, metrics):
    """Return [(metric, day, bytes)] for the requested sleep-derived metrics.

    Metrics with no valid data come back with bytes=None.
    """
    hrv_bytes, rhr_bytes, day = process_sleep_document(session)
    sleep_bytes, _ = process_sleep_times(session)
    available = {"hrv": hrv_bytes, "rhr": rhr_bytes, "sleep": sleep_bytes}
    return [(m, day, available[m]) for m in SLEEP_METRICS if m in metrics]


def parse_metrics(value):
    metrics = [m.strip() for m in value.split(",") if m.strip()]
    unknown = [m for m in metrics if m not in ALL_METRICS]
    if unknown or not metrics:
        raise argparse.ArgumentTypeError(
            f"Unknown metric(s): {', '.join(unknown) or '(none)'} — choose from {', '.join(ALL_METRICS)}"
        )
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Backfill Oura data to Azure Blob Storage")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--local", action="store_true", help="Write to test_output/ instead of blob storage")
    parser.add_argument("--metrics", type=parse_metrics, default=["sleep", "readiness"],
                        help=f"Comma-separated metrics to write (default: sleep,readiness; choose from {','.join(ALL_METRICS)})")
    args = parser.parse_args()

    load_dotenv()
    for var in ("OURA_CLIENT_ID", "KEY_VAULT_URI"):
        if not os.getenv(var):
            print(f"Missing {var} — set it in .env or environment")
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

    def write(metric, day, data):
        if args.local:
            p = output_dir / blob_path(metric, day)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        else:
            from azure.storage.blob import ContentSettings
            container_client.upload_blob(
                blob_path(metric, day), data, overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )

    print("Refreshing access token (via Key Vault)...")
    access_token = refresh_access_token()
    print("OK\n")

    written = {m: 0 for m in args.metrics}
    missing = {m: [] for m in args.metrics}

    # --- Sleep-derived metrics (hrv, rhr, sleep) ---
    sleep_metrics = [m for m in args.metrics if m in SLEEP_METRICS]
    if sleep_metrics:
        print("Fetching sleep sessions...")
        sessions = fetch_all(access_token, "sleep", start, end)
        by_day = pick_overnight_by_day(sessions)
        print(f"{len(sessions)} session(s), {len(by_day)} night(s) of overnight sleep\n")

        for day in sorted(by_day):
            for metric, file_day, data in build_sleep_files(by_day[day], sleep_metrics):
                if data:
                    write(metric, file_day, data)
                    written[metric] += 1
                else:
                    missing[metric].append(file_day)

    # --- Readiness ---
    if "readiness" in args.metrics:
        print("\nFetching daily readiness...")
        docs = fetch_all(access_token, "daily_readiness", start, end)
        print(f"{len(docs)} readiness doc(s)\n")
        for doc in docs:
            data, day = process_readiness_document(doc)
            if data:
                write("readiness", day, data)
                written["readiness"] += 1
            else:
                missing["readiness"].append(day)

    # --- Summary ---
    total_days = (end - start).days + 1
    dest = "test_output/" if args.local else "blob storage"
    print(f"\nDone — wrote to {dest}. Range: {start} to {end} ({total_days} days)")
    for metric in args.metrics:
        no_data = total_days - written[metric]
        line = f"  {metric:<10} {written[metric]:>4} file(s), {no_data} day(s) with no data"
        if missing[metric]:
            line += f" (incl. {len(missing[metric])} with a session but no valid {metric})"
        print(line)


if __name__ == "__main__":
    main()
