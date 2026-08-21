# -----------------------------------------------------------------------
# webhook_receiver.py — HTTP-triggered Azure Function
# -----------------------------------------------------------------------
# This function is the heart of the pipeline. It sits at a public URL
# (e.g. https://ourahrv-func.azurewebsites.net/api/webhook) and handles
# two kinds of requests:
#
# 1. GET — Oura's verification challenge. When you first register a
#    webhook subscription, Oura sends a GET request with a "challenge"
#    query param. We echo it back to prove we own the URL. This only
#    happens once per subscription creation.
#
# 2. POST — Actual webhook notifications. Every time your Oura ring
#    syncs sleep data, Oura sends a POST here with a small JSON payload
#    like {"data_type": "sleep", "event_type": "create", "object_id": "abc123"}.
#    The payload is just a pointer — we then call back to Oura's API
#    to fetch the full sleep document, extract HRV + RHR from it, and
#    upload the results to Azure Blob Storage.
#
# Security: Every POST from Oura includes an HMAC signature header
# (x-oura-signature). We verify it before processing to ensure the
# request actually came from Oura, not someone random hitting our URL.
# -----------------------------------------------------------------------

import json
import logging
import os

import azure.functions as func

from core.blob_store import upload_blob
from core.data_processor import process_sleep_document
from core.oura_client import fetch_sleep_document, get_client_credentials
from core.security import verify_hmac

logger = logging.getLogger(__name__)

# A Blueprint is how Azure Functions v2 (Python) organizes functions
# across multiple files. Each Blueprint defines routes/triggers, and
# the main function_app.py registers them all.
bp = func.Blueprint()


# Optional token for subscription verification — set this in your app
# settings if you want an extra layer of verification during the initial
# subscription handshake. Oura sends this back as "verification_token"
# in the challenge request so you can confirm it's the subscription you
# initiated.
VERIFICATION_TOKEN = os.environ.get("OURA_WEBHOOK_VERIFICATION_TOKEN", "")


# auth_level=ANONYMOUS means no Azure function key is required to call this URL.
# This is intentional — Oura can't send function keys with its webhooks.
# Security comes from HMAC verification instead.
@bp.route(route="webhook", methods=["POST", "GET"], auth_level=func.AuthLevel.ANONYMOUS)
def webhook_receiver(req: func.HttpRequest) -> func.HttpResponse:

    # --- Part 1: Handle subscription verification (GET) ---
    # This only happens when you first create a webhook subscription via
    # scripts/create_subscription.py. Oura sends a GET request with a
    # "challenge" query parameter, and we must echo it back as JSON.
    if req.method == "GET":
        token = req.params.get("verification_token", "")
        challenge = req.params.get("challenge", "")
        if challenge:
            if VERIFICATION_TOKEN and token != VERIFICATION_TOKEN:
                logger.warning("Verification token mismatch")
                return func.HttpResponse("Unauthorized", status_code=401)
            logger.info("Responding to Oura verification challenge")
            return func.HttpResponse(
                json.dumps({"challenge": challenge}),
                status_code=200,
                mimetype="application/json",
            )
        return func.HttpResponse("OK", status_code=200)

    # --- Part 2: Handle incoming webhook notification (POST) ---
    body = req.get_body()

    # Verify the HMAC signature to confirm this request came from Oura.
    # Oura computes: HMAC-SHA256(client_secret, timestamp + body) and
    # sends it in the x-oura-signature header (hex-encoded, uppercased).
    signature = req.headers.get("x-oura-signature", "")
    timestamp = req.headers.get("x-oura-timestamp", "")
    _, client_secret = get_client_credentials()
    if not verify_hmac(body, timestamp, signature, client_secret):
        logger.warning("HMAC verification failed — rejecting request")
        return func.HttpResponse("Forbidden", status_code=403)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.error("Invalid JSON in webhook body")
        return func.HttpResponse("Bad Request", status_code=400)

    event_type = payload.get("event_type")
    data_type = payload.get("data_type")

    # We only care about sleep data — ignore anything else Oura might send
    if data_type != "sleep":
        logger.info(f"Ignoring non-sleep event: {data_type}/{event_type}")
        return func.HttpResponse("OK", status_code=200)

    # We handle both "create" (first sync) and "update" (Oura revises
    # sleep data hours later with improved HRV/staging analysis)
    if event_type not in ("create", "update"):
        logger.info(f"Ignoring event type: {event_type}")
        return func.HttpResponse("OK", status_code=200)

    sleep_id = payload.get("object_id")
    if not sleep_id:
        logger.error("No object_id in webhook payload")
        return func.HttpResponse("Bad Request", status_code=400)

    logger.info(f"Processing sleep/{event_type} for id={sleep_id}")

    # The webhook payload only tells us *what changed* — it doesn't include
    # the actual data. We need to call back to Oura's API to get the full
    # sleep document (which contains HRV time series, heart rate, etc.)
    sleep_doc = fetch_sleep_document(sleep_id)

    # Extract HRV and RHR using the same transform logic that the old
    # GitHub Actions pipeline used. The output JSON shape is identical
    # so the Apple Shortcut doesn't need to change.
    hrv_bytes, rhr_bytes, sleep_date = process_sleep_document(sleep_doc)

    # Upload the results to Azure Blob Storage at a dated path like
    # hrv/2026/08/2026-08-21_sleep-hrv.json
    if hrv_bytes:
        blob_name = upload_blob("hrv", sleep_date, hrv_bytes)
        logger.info(f"Wrote {blob_name}")
    else:
        logger.warning(f"No valid HRV data for {sleep_date}")

    if rhr_bytes:
        blob_name = upload_blob("rhr", sleep_date, rhr_bytes)
        logger.info(f"Wrote {blob_name}")
    else:
        logger.warning(f"No valid RHR data for {sleep_date}")

    return func.HttpResponse("OK", status_code=200)
