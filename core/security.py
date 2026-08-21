# -----------------------------------------------------------------------
# security.py — HMAC signature verification for Oura webhooks
# -----------------------------------------------------------------------
# Every POST that Oura sends to our webhook includes a signature in the
# x-oura-signature header. This lets us verify the request actually came
# from Oura and wasn't forged by someone who discovered our webhook URL.
#
# How it works:
#   1. Oura concatenates the timestamp + raw request body
#   2. Computes HMAC-SHA256 using your client secret as the key
#   3. Hex-encodes the result and uppercases it
#   4. Sends it as the x-oura-signature header
#
# We repeat the same computation and compare. If they match, the request
# is authentic. We use hmac.compare_digest() for timing-safe comparison
# (prevents timing attacks where an attacker could guess the signature
# one character at a time by measuring response times).
# -----------------------------------------------------------------------

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


def verify_hmac(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    """Verify the x-oura-signature HMAC.

    Args:
        body: Raw request body bytes (must be the original bytes, not re-serialized JSON)
        timestamp: Value of x-oura-timestamp header
        signature: Value of x-oura-signature header (hex, uppercased)
        secret: Oura client secret (the HMAC key)

    Returns True if the signature is valid.
    """
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(timestamp.encode("utf-8"))
    mac.update(body)
    expected = mac.hexdigest().upper()
    return hmac.compare_digest(expected, signature)
