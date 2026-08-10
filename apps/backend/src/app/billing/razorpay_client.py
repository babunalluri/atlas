from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.core.settings import Settings

RAZORPAY_API = "https://api.razorpay.com/v1"


class RazorpayError(Exception):
    """Raised when Razorpay API calls fail."""


class RazorpayClient:
    def __init__(self, settings: Settings) -> None:
        key_id = settings.razorpay_key_id.strip()
        secret = settings.razorpay_key_secret.get_secret_value().strip()
        if not key_id or not secret:
            raise ValueError("Razorpay key id and secret are required")
        self._auth = (key_id, secret)
        self._currency = settings.billing_currency.upper()

    async def create_payment_link(
        self,
        *,
        amount_paise: int,
        description: str,
        customer_name: str,
        callback_url: str,
        notes: dict[str, str],
        reference_id: str,
    ) -> dict[str, Any]:
        body = {
            "amount": amount_paise,
            "currency": self._currency,
            "accept_partial": False,
            "description": description[:255],
            "reference_id": reference_id[:40],
            "callback_url": callback_url,
            "callback_method": "get",
            "customer": {"name": customer_name[:255]},
            "notes": notes,
        }
        return await self._post("/payment_links", body)

    async def create_subscription(
        self,
        *,
        plan_id: str,
        total_count: int,
        customer_id: str | None,
        notes: dict[str, str],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "plan_id": plan_id,
            "total_count": total_count,
            "customer_notify": 1,
            "notes": notes,
        }
        if customer_id:
            body["customer_id"] = customer_id
        return await self._post("/subscriptions", body)

    async def create_customer(self, *, name: str, notes: dict[str, str]) -> dict[str, Any]:
        return await self._post(
            "/customers",
            {"name": name[:255], "notes": notes},
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=RAZORPAY_API, timeout=30.0) as client:
            response = await client.post(path, json=body, auth=self._auth)
        if response.status_code >= 400:
            raise RazorpayError(
                f"Razorpay {path} failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RazorpayError(f"Unexpected Razorpay response for {path}")
        return payload


def verify_webhook_signature(
    body: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def parse_webhook_notes(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def webhook_payment_notes(payload: dict[str, Any]) -> dict[str, str]:
    entity = payload.get("payload", {})
    if not isinstance(entity, dict):
        return {}
    for key in ("payment_link", "payment", "subscription"):
        block = entity.get(f"{key}.entity") or entity.get(key)
        if isinstance(block, dict):
            notes = parse_webhook_notes(block.get("notes"))
            if notes:
                return notes
    payment = payload.get("payload", {}).get("payment", {}).get("entity")
    if isinstance(payment, dict):
        return parse_webhook_notes(payment.get("notes"))
    return {}


def webhook_event_id(payload: dict[str, Any]) -> str:
    return str(payload.get("id") or json.dumps(payload, sort_keys=True)[:120])
