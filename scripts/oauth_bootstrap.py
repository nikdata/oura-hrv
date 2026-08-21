#!/usr/bin/env python3
"""Seed (or re-seed) the Oura OAuth refresh token into Azure Key Vault.

Run this locally:
  1. First time: to complete the OAuth authorization flow and store the initial token.
  2. When the refresh chain dies (invalid_grant): to re-authorize and store a fresh token.

Usage:
    python scripts/oauth_bootstrap.py --vault-uri https://<name>.vault.azure.net/ \\
        --client-id <oura-client-id> --client-secret <oura-client-secret>

Requires: az login (the script uses DefaultAzureCredential to write to Key Vault).
"""
import argparse
import http.server
import threading
import urllib.parse
import webbrowser

import requests
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
REDIRECT_PORT = 8976
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"


def run_auth_flow(client_id: str, client_secret: str) -> dict:
    """Open browser for Oura OAuth, capture the callback, exchange for tokens."""
    auth_code_holder = {}
    server_ready = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            if "code" in params:
                auth_code_holder["code"] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Authorization successful — you can close this tab.")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing authorization code.")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), CallbackHandler)

    def serve():
        server_ready.set()
        server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    server_ready.wait()

    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "personal daily sleep heartrate",
    })
    auth_url = f"{AUTHORIZE_URL}?{params}"
    print(f"Opening browser for authorization...\n{auth_url}\n")
    webbrowser.open(auth_url)

    thread.join(timeout=120)
    server.server_close()

    if "code" not in auth_code_holder:
        raise RuntimeError("Did not receive authorization code within 120 seconds.")

    code = auth_code_holder["code"]
    print(f"Received authorization code: {code[:10]}...")

    response = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
    })
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description="Seed Oura OAuth tokens into Key Vault")
    parser.add_argument("--vault-uri", required=True, help="Key Vault URI")
    parser.add_argument("--client-id", required=True, help="Oura OAuth client ID")
    parser.add_argument("--client-secret", required=True, help="Oura OAuth client secret")
    args = parser.parse_args()

    tokens = run_auth_flow(args.client_id, args.client_secret)

    kv = SecretClient(vault_url=args.vault_uri, credential=DefaultAzureCredential())

    kv.set_secret("oura-refresh-token", tokens["refresh_token"])
    print("Stored oura-refresh-token in Key Vault")

    kv.set_secret("oura-client-secret", args.client_secret)
    print("Stored oura-client-secret in Key Vault")

    print(f"\nAccess token (for manual testing): {tokens['access_token'][:20]}...")
    print("Done. The Function can now read tokens via managed identity.")


if __name__ == "__main__":
    main()
