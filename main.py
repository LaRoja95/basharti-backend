"""Basharti (بشرتي) — cosmetics & skincare store API.

Clean, from-scratch FastAPI backend. Cash-on-delivery only. TikTok Pixel +
Events API (CAPI) tracking wired in from day one with the correct event
name ("CompletePayment"), avoiding the bug that broke tracking on the
older store this replaces.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from capi import dispatch_capi_event, log_capi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("basharti.api")

API_BUILD = "basharti-api-1.0.0"

KSA_PHONE_RE = re.compile(r"^5\d{8}$")

# --- Product catalog -------------------------------------------------------
# Placeholder starter catalog — replace with real products/prices/images
# before going live.
PRODUCTS: dict[str, dict[str, Any]] = {
    "serum-vitc": {
        "name": "سيروم فيتامين سي للوجه",
        "description": "سيروم مضاد للأكسدة لتفتيح وتوحيد لون البشرة",
        "price": 89,
        "image": "",
    },
    "cream-hydra": {
        "name": "كريم ترطيب مكثف",
        "description": "ترطيب عميق لمدة 24 ساعة لجميع أنواع البشرة",
        "price": 69,
        "image": "",
    },
    "sunscreen-spf50": {
        "name": "واقي شمس SPF 50",
        "description": "حماية عالية من أشعة الشمس بملمس خفيف غير دهني",
        "price": 75,
        "image": "",
    },
    "cleanser-gentle": {
        "name": "غسول لطيف للوجه",
        "description": "ينظف دون أن يجرد البشرة من رطوبتها الطبيعية",
        "price": 49,
        "image": "",
    },
}


# --- Shipping regions (Saudi Arabia) ---------------------------------------
# Cash-on-delivery shipping cost per region (SAR). Adjust as needed.
REGIONS: dict[str, dict[str, Any]] = {
    "riyadh": {"name": "الرياض", "shippingCost": 20},
    "makkah": {"name": "مكة المكرمة", "shippingCost": 25},
    "madinah": {"name": "المدينة المنورة", "shippingCost": 25},
    "eastern": {"name": "المنطقة الشرقية", "shippingCost": 25},
    "qassim": {"name": "القصيم", "shippingCost": 30},
    "asir": {"name": "عسير", "shippingCost": 35},
    "tabuk": {"name": "تبوك", "shippingCost": 35},
    "hail": {"name": "حائل", "shippingCost": 30},
    "northern_borders": {"name": "الحدود الشمالية", "shippingCost": 40},
    "jazan": {"name": "جازان", "shippingCost": 35},
    "najran": {"name": "نجران", "shippingCost": 40},
    "bahah": {"name": "الباحة", "shippingCost": 35},
    "jouf": {"name": "الجوف", "shippingCost": 40},
}


def get_region(region_id: str) -> dict[str, Any]:
    region = REGIONS.get(region_id)
    if not region:
        raise HTTPException(status_code=422, detail="منطقة غير صالحة")
    return region


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def normalize_phone(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", raw_phone)
    if not KSA_PHONE_RE.fullmatch(digits):
        raise HTTPException(status_code=422, detail="رقم الجوال يجب أن يبدأ بـ 5 ويتكون من 9 أرقام")
    return "+966" + digits


def client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else ""


def generate_order_id(created: datetime) -> str:
    suffix = secrets.token_hex(3).upper()
    return f"bshr{created.strftime('%m%d%Y')}{suffix}"


# --- Request / response models ---------------------------------------------
class OrderItem(BaseModel):
    productId: str
    quantity: int = Field(ge=1, le=10)


class PrepareOrderRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=9, max_length=20)
    regionId: str = Field(min_length=1, max_length=40)
    city: str = Field(min_length=2, max_length=60)
    address: str = Field(min_length=5, max_length=240)
    items: list[OrderItem] = Field(min_length=1)
    eventId: str = ""


class CompleteOrderRequest(BaseModel):
    orderId: str
    eventId: str = ""


class TrackingEventRequest(BaseModel):
    eventName: str
    eventId: str
    orderId: str | None = None
    payload: dict[str, Any] = {}


def validate_items(items: list[OrderItem]) -> tuple[list[dict[str, Any]], int]:
    clean_items: list[dict[str, Any]] = []
    subtotal = 0
    for item in items:
        product = PRODUCTS.get(item.productId)
        if not product:
            raise HTTPException(status_code=422, detail="منتج غير صالح")
        line_total = product["price"] * item.quantity
        clean_items.append(
            {
                "product_id": item.productId,
                "name": product["name"],
                "price": product["price"],
                "quantity": item.quantity,
            }
        )
        subtotal += line_total
    return clean_items, subtotal


def health_payload() -> dict[str, Any]:
    pixel_id = os.getenv("TIKTOK_PIXEL_ID", "").strip()
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    return {
        "status": "ok",
        "service": "basharti-api",
        "build": API_BUILD,
        "productsCount": len(PRODUCTS),
        "tiktokConfigured": bool(pixel_id and token),
    }


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return url.replace("postgres://", "postgresql://", 1)


async def ensure_schema(conn: "asyncpg.Connection") -> None:
    await conn.execute(
        """
        create table if not exists orders (
            id text primary key,
            created_at timestamptz not null,
            status text not null,
            name text not null,
            phone_raw text not null,
            phone_e164 text not null,
            city text not null,
            address text not null,
            items jsonb not null,
            total_sar integer not null,
            event_id text not null
        );
        """
    )
    # Migration-safe: add shipping columns for stores created before regions existed.
    await conn.execute("alter table orders add column if not exists region_id text not null default 'riyadh'")
    await conn.execute("alter table orders add column if not exists shipping_sar integer not null default 0")
    await conn.execute(
        """
        create table if not exists tracking_events (
            id bigserial primary key,
            created_at timestamptz not null,
            event_name text not null,
            event_id text not null,
            order_id text,
            payload jsonb not null
        );
        """
    )
    await conn.execute("create index if not exists idx_orders_created_at on orders (created_at desc)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    url = database_url()
    logger.info("STARTUP: connecting to Postgres ...")
    app.state.pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    async with app.state.pool.acquire() as conn:
        await ensure_schema(conn)
    logger.info("STARTUP OK: DB connected, tables ensured")
    yield
    await app.state.pool.close()
    logger.info("SHUTDOWN: Postgres pool closed")


app = FastAPI(title="Basharti API", version="1.0.0", lifespan=lifespan)

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, Any]:
    return health_payload()


@app.get("/health")
async def health() -> dict[str, Any]:
    return health_payload()


@app.get("/api/products")
async def list_products() -> list[dict[str, Any]]:
    return [{"id": pid, **data} for pid, data in PRODUCTS.items()]


@app.get("/api/products/{product_id}")
async def get_product(product_id: str) -> dict[str, Any]:
    product = PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"id": product_id, **product}


@app.get("/api/regions")
async def list_regions() -> list[dict[str, Any]]:
    return [{"id": rid, **data} for rid, data in REGIONS.items()]


@app.post("/api/orders/prepare")
async def prepare_order(payload: PrepareOrderRequest, request: Request) -> dict[str, Any]:
    phone_e164 = normalize_phone(payload.phone)
    region = get_region(payload.regionId)
    clean_items, subtotal = validate_items(payload.items)
    shipping = region["shippingCost"]
    total = subtotal + shipping

    order_id = generate_order_id(now_dt())
    created = now_dt()

    async with request.app.state.pool.acquire() as conn:
        await conn.execute(
            """
            insert into orders
                (id, created_at, status, name, phone_raw, phone_e164, city, address, items, total_sar, event_id, region_id, shipping_sar)
            values ($1, $2, 'pending', $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)
            """,
            order_id,
            created,
            payload.name.strip(),
            re.sub(r"\D", "", payload.phone),
            phone_e164,
            payload.city.strip(),
            payload.address.strip(),
            json.dumps(clean_items, ensure_ascii=False),
            total,
            payload.eventId,
            payload.regionId,
            shipping,
        )

    return {
        "orderId": order_id,
        "subtotal": subtotal,
        "shipping": shipping,
        "total": total,
        "regionName": region["name"],
    }


@app.post("/api/orders/complete")
async def complete_order(payload: CompleteOrderRequest, request: Request) -> dict[str, Any]:
    async with request.app.state.pool.acquire() as conn:
        row = await conn.fetchrow("select * from orders where id = $1", payload.orderId)
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        if row["status"] == "completed":
            return {"orderId": payload.orderId, "status": "completed"}

        await conn.execute(
            "update orders set status = 'completed', event_id = $2 where id = $1",
            payload.orderId,
            payload.eventId,
        )

    ip = client_ip(request)
    user_agent = request.headers.get("user-agent", "")
    items = json.loads(row["items"]) if isinstance(row["items"], str) else row["items"]

    capi_payload: dict[str, Any] = {
        "value": row["total_sar"],
        "currency": "SAR",
        "phone": row["phone_e164"],
        "productIds": [item["product_id"] for item in items],
    }
    site_url = os.getenv("SITE_URL", "").strip().rstrip("/")
    if site_url:
        capi_payload["pageUrl"] = f"{site_url}/?order={payload.orderId}"

    asyncio.create_task(
        dispatch_capi_event(
            event_name="CompletePayment",
            event_id=payload.eventId or secrets.token_hex(8),
            payload=capi_payload,
            ip=ip,
            user_agent=user_agent,
        )
    )

    return {"orderId": payload.orderId, "status": "completed"}


@app.post("/api/e")
async def tracking_event(payload: TrackingEventRequest, request: Request) -> dict[str, bool]:
    ip = client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    async with request.app.state.pool.acquire() as conn:
        await conn.execute(
            """
            insert into tracking_events (created_at, event_name, event_id, order_id, payload)
            values ($1, $2, $3, $4, $5::jsonb)
            """,
            now_dt(),
            payload.eventName,
            payload.eventId,
            payload.orderId,
            json.dumps(payload.payload, ensure_ascii=False),
        )

    if payload.eventName != "CompletePayment":
        # CompletePayment is dispatched from /api/orders/complete once the
        # order is actually confirmed server-side, so it isn't re-fired here.
        asyncio.create_task(
            dispatch_capi_event(
                event_name=payload.eventName,
                event_id=payload.eventId,
                payload=payload.payload,
                ip=ip,
                user_agent=user_agent,
            )
        )

    return {"ok": True}


@app.get("/api/admin/orders")
async def admin_orders(request: Request, x_admin_token: str = Header(default="")) -> list[dict[str, Any]]:
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch("select * from orders order by created_at desc limit 200")

    result = []
    for row in rows:
        order = dict(row)
        order["created_at"] = order["created_at"].isoformat()
        order["items"] = json.loads(order["items"]) if isinstance(order["items"], str) else order["items"]
        result.append(order)
    return result
