"""Meta Conversions API (server-side) for Basharti.

Mirrors the TikTok CAPI pattern in capi.py: fire-and-forget async dispatch,
shared event_id with the browser pixel for deduplication, hashed PII only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger("basharti.meta_capi")

META_GRAPH_VERSION = os.getenv("META_GRAPH_API_VERSION", "v19.0").strip() or "v19.0"

# Internal storefront event names -> Meta standard event names
EVENT_NAME_MAP: dict[str, str] = {
    "CompletePayment": "Purchase",
    "PageView": "PageView",
    "ViewContent": "ViewContent",
    "AddToCart": "AddToCart",
    "InitiateCheckout": "InitiateCheckout",
}

STANDARD_EVENTS = frozenset(EVENT_NAME_MAP)


def meta_logging_enabled() -> bool:
    return os.getenv("TRACKING_LOG_CAPI", "true").lower() == "true"


def log_meta(message: str, **fields: Any) -> None:
    if not meta_logging_enabled():
        return
    extras = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s %s", message, extras)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def meta_hash_phone(phone_raw: str) -> str:
    """Normalize to digits with country code (no +), then SHA-256."""
    digits = digits_only(phone_raw)
    if not digits:
        return ""
    if digits.startswith("966"):
        normalized = digits
    elif digits.startswith("0"):
        normalized = f"966{digits[1:]}"
    elif len(digits) == 9 and digits.startswith("5"):
        normalized = f"966{digits}"
    else:
        normalized = digits
    return sha256_hex(normalized)


def meta_hash_email(email: str) -> str:
    cleaned = email.strip().lower()
    return sha256_hex(cleaned) if cleaned else ""


def meta_config() -> tuple[str, str, str]:
    pixel_id = os.getenv("META_PIXEL_ID", "").strip()
    token = os.getenv("META_CAPI_ACCESS_TOKEN", "").strip()
    test_code = os.getenv("META_CAPI_TEST_EVENT_CODE", "").strip()
    return pixel_id, token, test_code


def meta_configured() -> bool:
    pixel_id, token, _ = meta_config()
    return bool(pixel_id and token)


async def send_meta_capi_event(
    *,
    event_name: str,
    event_id: str,
    payload: dict[str, Any],
    ip: str,
    user_agent: str,
) -> dict[str, Any]:
    pixel_id, token, test_code = meta_config()
    if not pixel_id or not token:
        log_meta("meta_capi_skip", reason="not_configured", event=event_name, event_id=event_id)
        return {"ok": False, "skipped": True, "reason": "not_configured"}

    meta_event = EVENT_NAME_MAP.get(event_name, event_name)

    user_data: dict[str, Any] = {}
    if ip:
        user_data["client_ip_address"] = ip
    if user_agent:
        user_data["client_user_agent"] = user_agent
    if payload.get("fbp"):
        user_data["fbp"] = payload["fbp"]
    if payload.get("fbc"):
        user_data["fbc"] = payload["fbc"]

    phone_hash = meta_hash_phone(payload.get("phone", ""))
    email_hash = meta_hash_email(payload.get("email", ""))
    if phone_hash:
        user_data["ph"] = [phone_hash]
    if email_hash:
        user_data["em"] = [email_hash]

    custom_data: dict[str, Any] = {}
    if payload.get("currency"):
        custom_data["currency"] = payload["currency"]
    if payload.get("value") is not None:
        custom_data["value"] = float(payload["value"])
    product_ids = payload.get("productIds") or payload.get("content_ids") or []
    if product_ids:
        custom_data["content_ids"] = product_ids
    if payload.get("orderId") and meta_event == "Purchase":
        custom_data["order_id"] = payload["orderId"]

    event_data: dict[str, Any] = {
        "event_name": meta_event,
        "event_time": int(time.time()),
        "event_id": event_id,
        "action_source": "website",
    }
    if payload.get("pageUrl"):
        event_data["event_source_url"] = payload["pageUrl"]
    if user_data:
        event_data["user_data"] = user_data
    if custom_data:
        event_data["custom_data"] = custom_data

    body: dict[str, Any] = {"data": [event_data]}
    if test_code:
        body["test_event_code"] = test_code

    url = f"https://graph.facebook.com/{META_GRAPH_VERSION}/{pixel_id}/events"
    params = {"access_token": token}

    log_meta("meta_capi_send", event=meta_event, event_id=event_id, pixel_id=pixel_id, test=bool(test_code))

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(url, params=params, json=body)
    except httpx.HTTPError as exc:
        logger.warning("meta_capi_error event_id=%s error=%s", event_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    ok = response.is_success
    summary = response.text[:500]
    try:
        data = response.json()
        summary = json.dumps(data, ensure_ascii=False)[:500]
        ok = response.is_success and "error" not in data
    except json.JSONDecodeError:
        pass

    if not ok:
        logger.warning("meta_capi_error event_id=%s status=%s body=%s", event_id, response.status_code, summary)

    log_meta("meta_capi_result", event=meta_event, event_id=event_id, status=response.status_code, ok=ok)
    return {"ok": ok, "skipped": False, "status": response.status_code, "summary": summary}


async def dispatch_meta_capi_event(
    *, event_name: str, event_id: str, payload: dict[str, Any], ip: str, user_agent: str
) -> dict[str, Any]:
    if event_name not in STANDARD_EVENTS:
        log_meta("meta_capi_skip", reason="unknown_event", event=event_name, event_id=event_id)
        return {"ok": False, "skipped": True, "reason": "unknown_event"}
    return await send_meta_capi_event(
        event_name=event_name, event_id=event_id, payload=payload, ip=ip, user_agent=user_agent
    )
