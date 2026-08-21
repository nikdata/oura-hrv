#!/usr/bin/env python3
"""Register Oura webhook subscriptions for sleep/create and sleep/update.

The receiver Function must be deployed and reachable before running this,
because Oura sends a verification challenge to the callback URL during creation.

Usage:
    python scripts/create_subscription.py \\
        --callback-url https://<func-app>.azurewebsites.net/api/webhook \\
        --client-id <oura-client-id> --client-secret <oura-client-secret>
"""
import argparse

import requests

API_BASE = "https://api.ouraring.com/v2"


def create_subscription(
    callback_url: str, client_id: str, client_secret: str,
    data_type: str, event_type: str, verification_token: str,
) -> dict:
    headers = {
        "x-client-id": client_id,
        "x-client-secret": client_secret,
        "Content-Type": "application/json",
    }
    body = {
        "callback_url": callback_url,
        "verification_token": verification_token,
        "data_type": data_type,
        "event_type": event_type,
    }
    response = requests.post(
        f"{API_BASE}/webhook/subscription", json=body, headers=headers,
    )
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description="Create Oura webhook subscriptions")
    parser.add_argument("--callback-url", required=True, help="Deployed webhook Function URL")
    parser.add_argument("--client-id", required=True, help="Oura app client ID")
    parser.add_argument("--client-secret", required=True, help="Oura app client secret")
    parser.add_argument("--verification-token", required=True,
                        help="Secret token echoed back during verification challenge")
    args = parser.parse_args()

    for event_type in ("create", "update"):
        print(f"Creating sleep/{event_type} subscription...")
        result = create_subscription(
            args.callback_url, args.client_id, args.client_secret,
            data_type="sleep", event_type=event_type,
            verification_token=args.verification_token,
        )
        print(f"  Created: id={result.get('id')}, status={result.get('status')}")

    print("\nDone. Both subscriptions registered.")
    print("Oura will send a verification challenge to your callback URL.")
    print("Check the Function logs to confirm it responded successfully.")


if __name__ == "__main__":
    main()
