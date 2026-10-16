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
#    syncs, Oura sends a POST here with a small JSON payload like
#    {"data_type": "sleep", "event_type": "create", "object_id": "abc123"}.
#    The payload is just a pointer — we then call back to Oura's API
#    to fetch the full document and upload the results to Azure Blob
#    Storage. Two data types are handled:
#      - sleep: overnight sessions only (naps are skipped). Produces
#        HRV, RHR and sleep-times files.
#      - daily_readiness: produces the readiness score file.
#    Oura has never actually delivered a daily_readiness webhook (the
#    subscriptions exist and renew daily, but no event arrives), so an
#    overnight sleep event ALSO fetches that day's readiness by date and
#    writes the readiness file. The daily_readiness branch stays in case
#    Oura starts sending them.
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
from core.data_processor import (
    is_overnight_sleep, process_readiness_document, process_sleep_document, process_sleep_times,
)
from core.oura_client import (
    fetch_readiness_document, fetch_readiness_for_day, fetch_sleep_document, get_client_credentials,
)
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

    # We only care about sleep and readiness — ignore anything else Oura might send
    if data_type not in ("sleep", "daily_readiness"):
        logger.info(f"Ignoring event: {data_type}/{event_type}")
        return func.HttpResponse("OK", status_code=200)

    # We handle both "create" (first sync) and "update" (Oura revises
    # data hours later with improved HRV/staging analysis)
    if event_type not in ("create", "update"):
        logger.info(f"Ignoring event type: {event_type}")
        return func.HttpResponse("OK", status_code=200)

    object_id = payload.get("object_id")
    if not object_id:
        logger.error("No object_id in webhook payload")
        return func.HttpResponse("Bad Request", status_code=400)

    logger.info(f"Processing {data_type}/{event_type} for id={object_id}")

    if data_type == "sleep":
        _handle_sleep(object_id)
    else:
        _handle_readiness(object_id)

    return func.HttpResponse("OK", status_code=200)


def _upload(metric: str, day: str, data: bytes | None):
    """Upload one file if there's data, otherwise log that it was missing."""
    if data:
        blob_name = upload_blob(metric, day, data)
        logger.info(f"Wrote {blob_name}")
    else:
        logger.warning(f"No valid {metric} data for {day}")


def _handle_sleep(sleep_id: str):
    # The webhook payload only tells us *what changed* — it doesn't include
    # the actual data. We need to call back to Oura's API to get the full
    # sleep document (which contains HRV time series, heart rate, bedtimes, etc.)
    sleep_doc = fetch_sleep_document(sleep_id)

    # Naps get their own sleep sessions with the same "day" as the night
    # before. Without this check, a nap would overwrite that day's files.
    if not is_overnight_sleep(sleep_doc):
        logger.info(f"Skipping nap/non-overnight sleep (type={sleep_doc.get('type')}) for {sleep_doc.get('day')}")
        return

    # Extract HRV and RHR using the same transform logic that the old
    # GitHub Actions pipeline used. The output JSON shape is identical
    # so the Apple Shortcut doesn't need to change.
    hrv_bytes, rhr_bytes, sleep_date = process_sleep_document(sleep_doc)
    sleep_bytes, _ = process_sleep_times(sleep_doc)

    # Upload the results to Azure Blob Storage at dated paths like
    # hrv/2026/08/2026-08-21_sleep-hrv.json
    _upload("hrv", sleep_date, hrv_bytes)
    _upload("rhr", sleep_date, rhr_bytes)
    _upload("sleep", sleep_date, sleep_bytes)

    # Readiness is scored from this same night, and Oura never sends us the
    # daily_readiness webhook — so fetch it by date now. Best effort: a
    # failure here must not fail the sleep event (Oura would retry it and
    # rewrite the sleep files). Later sleep/update events refresh it.
    try:
        _write_readiness_for_day(sleep_date)
    except Exception:
        logger.exception(f"Fetching readiness for {sleep_date} failed — sleep files were still written")


def _write_readiness_for_day(day: str):
    doc = fetch_readiness_for_day(day)
    if doc is None:
        logger.warning(f"No readiness document from Oura for {day} yet")
        return
    readiness_bytes, _ = process_readiness_document(doc)
    _upload("readiness", day, readiness_bytes)


def _handle_readiness(readiness_id: str):
    readiness_doc = fetch_readiness_document(readiness_id)
    readiness_bytes, day = process_readiness_document(readiness_doc)
    _upload("readiness", day, readiness_bytes)
