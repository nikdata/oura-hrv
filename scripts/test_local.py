#!/usr/bin/env python3
"""End-to-end local test — no Azure required.

Fetches real sleep data from Oura, runs it through the data processor,
and writes the output JSON files locally so you can inspect them.

Usage:
    1. Create .env with: OURA_CLIENT_ID, OURA_CLIENT_SECRET, OURA_REFRESH_TOKEN
    2. python scripts/test_local.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data_processor import process_sleep_document

TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "test_output"


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
        print(f"\nRefresh token (first 10): {os.environ['OURA_REFRESH_TOKEN'][:10]}...")
        raise SystemExit(1)
    tokens = response.json()

    if "refresh_token" in tokens and tokens["refresh_token"] != os.environ["OURA_REFRESH_TOKEN"]:
        print(f"NOTE: Oura rotated your refresh token. Update .env with:")
        print(f"  OURA_REFRESH_TOKEN={tokens['refresh_token']}")

    return tokens["access_token"]


def fetch_sleep_list(access_token, days=3):
    end = datetime.now().date() + timedelta(days=1)
    start = end - timedelta(days=days)
    url = f"{API_BASE}/usercollection/sleep?start_date={start}&end_date={end}"
    response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    response.raise_for_status()
    return response.json()


def fetch_sleep_by_id(access_token, sleep_id):
    url = f"{API_BASE}/usercollection/sleep/{sleep_id}"
    response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    response.raise_for_status()
    return response.json()


def main():
    load_dotenv()

    for var in ("OURA_CLIENT_ID", "OURA_CLIENT_SECRET", "OURA_REFRESH_TOKEN"):
        if not os.getenv(var):
            print(f"Missing {var} in .env")
            return

    print("Refreshing access token...")
    access_token = refresh_token()
    print("OK\n")

    print("Fetching recent sleep data (last 3 days)...")
    sleep_list = fetch_sleep_list(access_token)
    sessions = sleep_list.get("data", [])
    print(f"Found {len(sessions)} sleep session(s)\n")

    if not sessions:
        print("No sleep data found. Sync your ring in the Oura app and try again.")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)

    for session in sessions:
        sleep_id = session.get("id", "unknown")
        day = session.get("day", "unknown")
        print(f"--- {day} (id: {sleep_id[:20]}...) ---")

        # Test the webhook flow: fetch by ID (same as webhook_receiver would)
        print(f"  Fetching by ID...")
        doc = fetch_sleep_by_id(access_token, sleep_id)

        # Run through the processor
        hrv_bytes, rhr_bytes, sleep_date = process_sleep_document(doc)

        if hrv_bytes:
            hrv_path = OUTPUT_DIR / f"{sleep_date}_sleep-hrv.json"
            hrv_path.write_bytes(hrv_bytes)
            hrv_data = json.loads(hrv_bytes)
            print(f"  HRV: {len(hrv_data)} readings -> {hrv_path}")
        else:
            print(f"  HRV: no valid data")

        if rhr_bytes:
            rhr_path = OUTPUT_DIR / f"{sleep_date}_sleep-rhr.json"
            rhr_path.write_bytes(rhr_bytes)
            rhr_data = json.loads(rhr_bytes)
            print(f"  RHR: {rhr_data[0]['rhr']} bpm -> {rhr_path}")
        else:
            print(f"  RHR: no valid data")

        # Also dump the raw Oura response for debugging
        raw_path = OUTPUT_DIR / f"{sleep_date}_raw.json"
        raw_path.write_text(json.dumps(doc, indent=2))
        print(f"  Raw: {raw_path}")
        print()

    print(f"All output written to {OUTPUT_DIR}/")
    print("Compare HRV/RHR files against example/ fixtures to verify the shape is identical.")


if __name__ == "__main__":
    main()
