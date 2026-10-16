# -----------------------------------------------------------------------
# oura_client.py — Oura API client with OAuth2 and Key Vault integration
# -----------------------------------------------------------------------
# Handles all communication with the Oura API, including:
#
# 1. OAuth2 token management:
#    - Reads the refresh token from Azure Key Vault
#    - Exchanges it for an access token (used to call Oura's API)
#    - If Oura rotates the refresh token, writes the new one back to
#      Key Vault so the chain doesn't break
#    - Detects "invalid_grant" (dead token chain) and tells you to
#      re-run oauth_bootstrap.py
#
# 2. Document fetching:
#    - Fetches a single sleep or daily readiness document by ID (used by
#      the webhook receiver), and a day's readiness by date (fetched
#      alongside each night's sleep — see fetch_readiness_for_day)
#    - Automatic retry on 401 (expired access token)
#
# 3. Webhook subscription management:
#    - List and renew subscriptions (used by renew_subscriptions function)
#    - Uses client credentials (x-client-id/x-client-secret headers),
#      NOT the OAuth bearer token
#
# Why Key Vault (not app settings)?
#   The refresh token rotates — Oura may return a new one each time you
#   use it. Writing a rotated token back to app settings requires the
#   Azure management API (hacky). Key Vault has a clean set_secret() API.
# -----------------------------------------------------------------------

import logging
import os

import requests
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2"


def _get_kv_client() -> SecretClient:
    """Create a Key Vault client using managed identity (in Azure)
    or az-login credentials (locally)."""
    vault_uri = os.environ["KEY_VAULT_URI"]
    return SecretClient(vault_url=vault_uri, credential=DefaultAzureCredential())


def _read_secret(name: str) -> str:
    return _get_kv_client().get_secret(name).value


def _write_secret(name: str, value: str):
    _get_kv_client().set_secret(name, value)


def get_client_credentials() -> tuple[str, str]:
    """Get Oura client ID (from app settings) and client secret (from Key Vault).

    The client ID is not sensitive and lives in app settings for simplicity.
    The client secret is sensitive and lives in Key Vault.
    """
    client_id = os.environ["OURA_CLIENT_ID"]
    client_secret = _read_secret("oura-client-secret")
    return client_id, client_secret


def refresh_access_token() -> str:
    """Exchange the refresh token for a new access token.

    If Oura returns a rotated refresh token, writes it back to Key Vault
    so future refreshes don't fail.

    Raises RuntimeError with actionable guidance on invalid_grant
    (meaning the token chain is dead and you need to re-authorize).
    """
    refresh_token = _read_secret("oura-refresh-token")
    client_id, client_secret = get_client_credentials()

    response = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    })

    if response.status_code == 400:
        try:
            body = response.json()
        except Exception:
            body = response.text
        if "invalid_grant" in str(body).lower():
            raise RuntimeError(
                "Oura refresh token is dead (invalid_grant). "
                "Run scripts/oauth_bootstrap.py to re-authorize and seed a new token."
            )
        raise RuntimeError(f"Token refresh failed (400): {body}")

    response.raise_for_status()
    tokens = response.json()

    access_token = tokens["access_token"]

    # Oura may rotate the refresh token — if so, persist the new one
    if "refresh_token" in tokens:
        new_refresh = tokens["refresh_token"]
        if new_refresh != refresh_token:
            logger.info("Oura rotated the refresh token — writing back to Key Vault")
            _write_secret("oura-refresh-token", new_refresh)

    return access_token


def fetch_document(collection: str, doc_id: str) -> dict:
    """Fetch a single document by ID, with automatic token refresh on 401.

    The webhook payload only contains the document's ID. This function
    calls back to Oura's API to get the full document. `collection` is the
    API path segment, e.g. "sleep" or "daily_readiness".
    """
    access_token = refresh_access_token()

    url = f"{API_BASE}/usercollection/{collection}/{doc_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    # Access tokens can expire between the refresh and the API call
    # (rare but possible). Retry once with a fresh token.
    if response.status_code == 401:
        logger.warning(f"Got 401 fetching {collection} doc, refreshing token and retrying")
        access_token = refresh_access_token()
        headers["Authorization"] = f"Bearer {access_token}"
        response = requests.get(url, headers=headers)

    response.raise_for_status()
    return response.json()


def fetch_sleep_document(sleep_id: str) -> dict:
    """Fetch a sleep session (HRV time series, heart rate, bedtimes, etc.)."""
    return fetch_document("sleep", sleep_id)


def fetch_readiness_document(readiness_id: str) -> dict:
    """Fetch a daily readiness document (score + contributors)."""
    return fetch_document("daily_readiness", readiness_id)


def fetch_readiness_for_day(day: str) -> dict | None:
    """Fetch the daily readiness document for one day (YYYY-MM-DD), or None.

    Oura has never delivered our daily_readiness webhooks (the subscriptions
    exist and renew, but no event ever arrives), so the webhook receiver
    calls this when a night's sleep comes in. Unlike the sleep list, the
    daily_readiness list includes its end_date, so day..day returns that day.
    """
    access_token = refresh_access_token()

    url = f"{API_BASE}/usercollection/daily_readiness"
    params = {"start_date": day, "end_date": day}
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 401:
        logger.warning("Got 401 fetching daily_readiness list, refreshing token and retrying")
        headers["Authorization"] = f"Bearer {refresh_access_token()}"
        response = requests.get(url, headers=headers, params=params)

    response.raise_for_status()
    return next((d for d in response.json().get("data", []) if d.get("day") == day), None)


# -----------------------------------------------------------------------
# Webhook subscription management
# -----------------------------------------------------------------------
# These functions use client credentials (x-client-id + x-client-secret
# headers), NOT the OAuth bearer token. This is a separate auth mechanism
# that Oura uses for subscription management.
# -----------------------------------------------------------------------


def list_webhook_subscriptions() -> list[dict]:
    """List all webhook subscriptions using client credentials."""
    client_id, client_secret = get_client_credentials()
    headers = {
        "x-client-id": client_id,
        "x-client-secret": client_secret,
    }
    response = requests.get(f"{API_BASE}/webhook/subscription", headers=headers)
    response.raise_for_status()
    return response.json()


def renew_webhook_subscription(subscription_id: str) -> dict:
    """Renew a webhook subscription to prevent expiry."""
    client_id, client_secret = get_client_credentials()
    headers = {
        "x-client-id": client_id,
        "x-client-secret": client_secret,
    }
    response = requests.put(
        f"{API_BASE}/webhook/subscription/renew/{subscription_id}",
        headers=headers,
    )
    response.raise_for_status()
    return response.json()
