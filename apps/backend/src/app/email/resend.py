"""Resend HTTP helpers + Svix webhook signature verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

RESEND_API = "https://api.resend.com"


class ResendError(RuntimeError):
    pass


def verify_svix_signature(
    *,
    body: bytes,
    headers: dict[str, str],
    secret: str,
    tolerance_seconds: int = 300,
) -> None:
    """Validate Resend/Svix webhook signatures.

    Raises ``ResendError`` when verification fails.
    """
    secret = (secret or "").strip()
    if not secret:
        raise ResendError("RESEND_WEBHOOK_SECRET is not configured")

    msg_id = headers.get("svix-id") or headers.get("Svix-Id")
    timestamp = headers.get("svix-timestamp") or headers.get("Svix-Timestamp")
    signature_header = headers.get("svix-signature") or headers.get("Svix-Signature")
    if not msg_id or not timestamp or not signature_header:
        raise ResendError("Missing Svix signature headers")

    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise ResendError("Invalid Svix timestamp") from exc
    if abs(int(time.time()) - ts) > tolerance_seconds:
        raise ResendError("Svix timestamp outside tolerance")

    raw_secret = secret
    if raw_secret.startswith("whsec_"):
        raw_secret = raw_secret[len("whsec_") :]
    try:
        key = base64.b64decode(raw_secret)
    except Exception as exc:
        raise ResendError("Invalid webhook secret encoding") from exc

    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    expected = f"v1,{digest}"
    candidates = [part.strip() for part in signature_header.split(" ")]
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        # Also accept bare v1 signatures without comparing other versions.
        if not any(
            hmac.compare_digest(digest, part.split(",", 1)[-1])
            for part in candidates
            if part.startswith("v1,")
        ):
            raise ResendError("Invalid Svix signature")


async def fetch_received_email(api_key: str, email_id: str) -> dict[str, Any]:
    """Load inbound email content from Resend Receiving API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{RESEND_API}/emails/receiving/{email_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if response.status_code >= 400:
        raise ResendError(
            f"Failed to fetch received email ({response.status_code}): {response.text[:300]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ResendError("Unexpected Resend receiving payload")
    return payload


async def send_resend_email(
    api_key: str,
    *,
    from_address: str,
    to_address: str,
    subject: str,
    text: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
    if references:
        headers["References"] = references
    body: dict[str, Any] = {
        "from": from_address,
        "to": [to_address],
        "subject": subject,
        "text": text,
        "html": f"<pre style=\"font-family:inherit;white-space:pre-wrap\">{_escape_html(text)}</pre>",
    }
    if headers:
        body["headers"] = headers
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RESEND_API}/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            content=json.dumps(body),
        )
    if response.status_code >= 400:
        logger.warning(
            "resend_send_failed",
            status=response.status_code,
            body=response.text[:300],
        )
        raise ResendError(
            f"Failed to send email ({response.status_code}): {response.text[:300]}"
        )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
