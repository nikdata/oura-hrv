# -----------------------------------------------------------------------
# blob_store.py — Azure Blob Storage helpers
# -----------------------------------------------------------------------
# Handles uploading processed HRV/RHR JSON files to Azure Blob Storage.
#
# Blob Storage is Azure's object storage (like AWS S3). Files are stored
# in "containers" (like S3 buckets). Our container is called "oura-data"
# and is configured for public-read access, so the Apple Shortcut can
# fetch files over plain HTTPS without any authentication.
#
# The blob path is pre-sorted by date:
#   hrv/2026/08/2026-08-21_sleep-hrv.json
#   rhr/2026/08/2026-08-21_sleep-rhr.json
#
# Authentication: The Function App has a "managed identity" (think of it
# as an Azure-internal username/password that Azure manages for you).
# DefaultAzureCredential picks this up automatically — no connection
# strings or passwords in your code.
# -----------------------------------------------------------------------

import os

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


# Tell Blob Storage to serve these files as JSON (not the default
# application/octet-stream). This matters because the Apple Shortcut
# uses "Get Contents of URL" which parses the response based on
# Content-Type.
JSON_CONTENT = ContentSettings(content_type="application/json")


def _get_container_client():
    """Create a client pointing to our blob container.

    Uses DefaultAzureCredential which automatically picks up:
    - In Azure: the Function App's managed identity
    - Locally: your 'az login' session
    """
    account_url = os.environ["BLOB_ACCOUNT_URL"]
    container = os.environ.get("BLOB_CONTAINER_NAME", "oura-data")
    credential = DefaultAzureCredential()
    service = BlobServiceClient(account_url, credential=credential)
    return service.get_container_client(container)


def blob_path(metric: str, sleep_date: str) -> str:
    """Build the dated blob path: hrv/YYYY/MM/YYYY-MM-DD_sleep-hrv.json"""
    year, month, _ = sleep_date.split("-")
    return f"{metric}/{year}/{month}/{sleep_date}_sleep-{metric}.json"


def upload_blob(metric: str, sleep_date: str, data: bytes) -> str:
    """Upload JSON bytes to the dated blob path, overwriting if exists.

    Overwrite is intentional — when Oura sends a "sleep/update" event
    (revising HRV data hours after the initial sync), we want the latest
    version to replace the old one at the same path.

    Returns the blob name.
    """
    container = _get_container_client()
    name = blob_path(metric, sleep_date)
    container.upload_blob(name, data, overwrite=True, content_settings=JSON_CONTENT)
    return name
