"""عالم حواء — women's fashion watches store API (Algeria COD).

FastAPI backend for Alam Hawa. Cash-on-delivery only. TikTok Pixel +
Events API (CAPI) tracking with CompletePayment.
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
from meta_capi import dispatch_meta_capi_event, meta_configured
from sheets import schedule_order_sheet_sync, sheets_configured

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("alamhawa.api")

API_BUILD = "alam-hawa-api-1.0.0"

DZ_PHONE_RE = re.compile(r"^0[5-7]\d{8}$")

# Catalog — 3 trending women's fashion watches (DZD)
PRODUCTS: dict[str, dict[str, Any]] = {
    "watch-emerald": {
        "name": "ساعة الزمرد الذهبية — ميناء أخضر",
        "description": (
            "ساعة نسائية مستطيلة رفيعة بتصميم تانك، ميناء أخضر زمردي وسوار مشبّك ذهبي. "
            "حركة كوارتز — أنيقة للارتداء اليومي والمناسبات."
        ),
        "price": 3500,
        "image": "assets/products/watch-emerald/hero-photo.jpg",
        "problem": "ساعة تراند يومية",
        "dept": "watches",
    },
    "watch-pearl": {
        "name": "ساعة اللؤلؤة الوردية — روز غولد",
        "description": (
            "ساعة نسائية رفيعة بميناء لؤلؤي وسوار روز غولد مشبّك. "
            "خفيفة وأنيقة — للخدمة والمناسبات."
        ),
        "price": 3200,
        "image": "assets/products/watch-pearl/hero-photo.jpg",
        "problem": "ساعة أنيقة يومية",
        "dept": "watches",
    },
    "watch-tank": {
        "name": "ساعة التانك الذهبية المرصّعة",
        "description": (
            "ساعة تانك ذهبية بإطار لمّاع مرصّع وميناء أبيض أنيق. "
            "مثالية كهدية — علبة أنيقة مع الطلب."
        ),
        "price": 4500,
        "image": "assets/products/watch-tank/hero-photo.jpg",
        "problem": "ساعة هدية فاخرة",
        "dept": "watches",
    },
    "glasses-cat-eye": {
        "name": "نظارات كات آي — روز غولد",
        "description": (
            "نظارات نسائية بشكل كات آي وإطار روز غولد أنيق. "
            "عدسات بحماية UV — لمسة شبابية لكل يوم."
        ),
        "price": 2800,
        "image": "assets/products/glasses-cat-eye/hero-photo.jpg",
        "problem": "نظارات تراند",
        "dept": "glasses",
    },
    "glasses-oval": {
        "name": "نظارات بيضاوية شفافة",
        "description": (
            "نظارات بيضاوية ناعمة بلون مووف هادئ. "
            "خفيفة ومريحة — للجامعة والخدمة والخروجات."
        ),
        "price": 2500,
        "image": "assets/products/glasses-oval/hero-photo.jpg",
        "problem": "نظارات يومية",
        "dept": "glasses",
    },
    "glasses-aviator": {
        "name": "نظارات أفياتور نسائية ذهبية",
        "description": (
            "نظارات أفياتور ذهبية بلمسة أنثوية ناعمة. "
            "ستايل كلاسيك مع عدسات خفيفة وحماية UV."
        ),
        "price": 3000,
        "image": "assets/products/glasses-aviator/hero-photo.jpg",
        "problem": "نظارات كلاسيك",
        "dept": "glasses",
    },
    "pajama-rose": {
        "name": "بيجاما ساتان روز",
        "description": (
            "طقم ملابس نوم ساتان بلون روز غبار: قميص وبنطلون بملمس حريري ناعم. "
            "أنيق ومريح — مثالي للهدايا ولياليكِ."
        ),
        "price": 4200,
        "image": "assets/products/pajama-rose/hero-photo.jpg",
        "problem": "بيجاما ساتان",
        "dept": "clothing",
    },
    "pajama-cream": {
        "name": "بيجاما كريم بالدانتيل",
        "description": (
            "طقم ساتان كريمي بتفاصيل دانتيل ناعمة على الياقة. "
            "لمسة رومانسية فاخرة لملابس النوم."
        ),
        "price": 4500,
        "image": "assets/products/pajama-cream/hero-photo.jpg",
        "problem": "بيجاما دانتيل",
        "dept": "clothing",
    },
    "pajama-mauve": {
        "name": "بيجاما مووف قطنية",
        "description": (
            "طقم بيجاما قطني بلون مووف هادئ وتفصيلة نظيفة. "
            "مريح للتنفس — للنوم اليومي بكل راحة."
        ),
        "price": 3800,
        "image": "assets/products/pajama-mauve/hero-photo.jpg",
        "problem": "بيجاما قطن",
        "dept": "clothing",
    },
}


# --- Shipping regions (Algeria — 58 wilayas) --------------------------------
REGIONS: dict[str, dict[str, Any]] = {
    "alger": {"name": "الجزائر", "shippingCost": 400},
    "blida": {"name": "البليدة", "shippingCost": 450},
    "bouira": {"name": "البويرة", "shippingCost": 450},
    "tizi_ouzou": {"name": "تيزي وزو", "shippingCost": 450},
    "medea": {"name": "المدية", "shippingCost": 450},
    "boumerdes": {"name": "بومرداس", "shippingCost": 450},
    "tipaza": {"name": "تيبازة", "shippingCost": 450},
    "chlef": {"name": "الشلف", "shippingCost": 550},
    "laghouat": {"name": "الأغواط", "shippingCost": 550},
    "oum_el_bouaghi": {"name": "أم البواقي", "shippingCost": 550},
    "batna": {"name": "باتنة", "shippingCost": 550},
    "bejaia": {"name": "بجاية", "shippingCost": 550},
    "tebessa": {"name": "تبسة", "shippingCost": 550},
    "tlemcen": {"name": "تلمسان", "shippingCost": 550},
    "tiaret": {"name": "تيارت", "shippingCost": 550},
    "djelfa": {"name": "الجلفة", "shippingCost": 550},
    "jijel": {"name": "جيجل", "shippingCost": 550},
    "setif": {"name": "سطيف", "shippingCost": 550},
    "saida": {"name": "سعيدة", "shippingCost": 550},
    "skikda": {"name": "سكيكدة", "shippingCost": 550},
    "sidi_bel_abbes": {"name": "سيدي بلعباس", "shippingCost": 550},
    "annaba": {"name": "عنابة", "shippingCost": 550},
    "guelma": {"name": "قالمة", "shippingCost": 550},
    "constantine": {"name": "قسنطينة", "shippingCost": 550},
    "mostaganem": {"name": "مستغانم", "shippingCost": 550},
    "msila": {"name": "المسيلة", "shippingCost": 550},
    "mascara": {"name": "معسكر", "shippingCost": 550},
    "oran": {"name": "وهران", "shippingCost": 550},
    "bordj_bou_arreridj": {"name": "برج بوعريريج", "shippingCost": 550},
    "el_tarf": {"name": "الطارف", "shippingCost": 550},
    "tissemsilt": {"name": "تيسمسيلت", "shippingCost": 550},
    "khenchela": {"name": "خنشلة", "shippingCost": 550},
    "souk_ahras": {"name": "سوق أهراس", "shippingCost": 550},
    "mila": {"name": "ميلة", "shippingCost": 550},
    "ain_defla": {"name": "عين الدفلى", "shippingCost": 550},
    "ain_temouchent": {"name": "عين تموشنت", "shippingCost": 550},
    "relizane": {"name": "غليزان", "shippingCost": 550},
    "biskra": {"name": "بسكرة", "shippingCost": 700},
    "bechar": {"name": "بشار", "shippingCost": 700},
    "ouargla": {"name": "ورقلة", "shippingCost": 700},
    "el_bayadh": {"name": "البيض", "shippingCost": 700},
    "el_oued": {"name": "الوادي", "shippingCost": 700},
    "naama": {"name": "النعامة", "shippingCost": 700},
    "ghardaia": {"name": "غرداية", "shippingCost": 700},
    "ouled_djellal": {"name": "أولاد جلال", "shippingCost": 700},
    "touggourt": {"name": "تقرت", "shippingCost": 700},
    "el_meghaier": {"name": "المغير", "shippingCost": 700},
    "el_meniaa": {"name": "المنيعة", "shippingCost": 700},
    "adrar": {"name": "أدرار", "shippingCost": 900},
    "tamanrasset": {"name": "تمنراست", "shippingCost": 900},
    "illizi": {"name": "إليزي", "shippingCost": 900},
    "tindouf": {"name": "تندوف", "shippingCost": 900},
    "timimoun": {"name": "تيميمون", "shippingCost": 900},
    "bordj_badji_mokhtar": {"name": "برج باجي مختار", "shippingCost": 900},
    "beni_abbes": {"name": "بني عباس", "shippingCost": 900},
    "in_salah": {"name": "عين صالح", "shippingCost": 900},
    "in_guezzam": {"name": "عين قزام", "shippingCost": 900},
    "djanet": {"name": "جانت", "shippingCost": 900},
}


def get_region(region_id: str) -> dict[str, Any]:
    region = REGIONS.get(region_id)
    if not region:
        raise HTTPException(status_code=422, detail="ولاية غير صالحة")
    return region


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def normalize_phone(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", raw_phone)
    if digits.startswith("213"):
        rest = digits[3:]
        if rest.startswith("0"):
            rest = rest[1:]
        if len(rest) == 9 and rest[0] in "567":
            return "+213" + rest
    if DZ_PHONE_RE.fullmatch(digits):
        return "+213" + digits[1:]
    raise HTTPException(
        status_code=422,
        detail="رقم الهاتف يجب أن يبدأ بـ 05 أو 06 أو 07 (10 أرقام) أو +213",
    )


def client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else ""


def generate_order_id(created: datetime) -> str:
    suffix = secrets.token_hex(3).upper()
    return f"hawa{created.strftime('%m%d%Y')}{suffix}"


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
    fbp: str = ""
    fbc: str = ""
    eventSourceUrl: str = ""


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


def health_payload(db_connected: bool = False) -> dict[str, Any]:
    pixel_id = os.getenv("TIKTOK_PIXEL_ID", "").strip()
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    return {
        "status": "ok",
        "service": "alam-hawa-api",
        "build": API_BUILD,
        "productsCount": len(PRODUCTS),
        "tiktokConfigured": bool(pixel_id and token),
        "metaConfigured": meta_configured(),
        "dbConfigured": bool(os.getenv("DATABASE_URL", "").strip()),
        "dbConnected": db_connected,
        "sheetsConfigured": sheets_configured(),
    }


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return url.replace("postgres://", "postgresql://", 1)


async def get_pool(request: Request) -> asyncpg.Pool:
    pool = getattr(request.app.state, "pool", None)
    if pool is not None:
        return pool
    try:
        pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=5)
        async with pool.acquire() as conn:
            await ensure_schema(conn)
        request.app.state.pool = pool
        logger.info("DB pool connected (lazy init)")
        return pool
    except Exception:
        logger.exception("DB connection failed")
        raise HTTPException(status_code=503, detail="تعذر الاتصال بقاعدة البيانات حالياً")


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
    await conn.execute("alter table orders add column if not exists region_id text not null default 'alger'")
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
    app.state.pool = None
    if os.getenv("DATABASE_URL", "").strip():
        try:
            logger.info("STARTUP: connecting to Postgres ...")
            app.state.pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=5)
            async with app.state.pool.acquire() as conn:
                await ensure_schema(conn)
            logger.info("STARTUP OK: DB connected, tables ensured")
        except Exception:
            logger.exception("STARTUP: DB unavailable — catalog API still available")
    else:
        logger.warning("STARTUP: DATABASE_URL not set — orders disabled until configured")
    yield
    if app.state.pool is not None:
        await app.state.pool.close()
        logger.info("SHUTDOWN: Postgres pool closed")


app = FastAPI(title="Alam Hawa API", version="1.0.0", lifespan=lifespan)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,https://alam-hawa.pages.dev,https://www.alam-hawa.pages.dev",
    ).split(",")
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
async def root(request: Request) -> dict[str, Any]:
    return health_payload(db_connected=getattr(request.app.state, "pool", None) is not None)


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return health_payload(db_connected=getattr(request.app.state, "pool", None) is not None)


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

    pool = await get_pool(request)
    async with pool.acquire() as conn:
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
    pool = await get_pool(request)
    async with pool.acquire() as conn:
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
    order_dict = dict(row)
    region_name = REGIONS.get(row["region_id"], {}).get("name", row["region_id"])

    schedule_order_sheet_sync(order_dict, region_name, status="مؤكد")

    event_id = payload.eventId or secrets.token_hex(8)
    site_url = os.getenv("SITE_URL", "https://alam-hawa.pages.dev").strip().rstrip("/")
    page_url = payload.eventSourceUrl.strip() or f"{site_url}/thank-you.html?order={payload.orderId}"

    capi_payload: dict[str, Any] = {
        "value": row["total_sar"],
        "currency": "DZD",
        "phone": row["phone_e164"],
        "productIds": [item["product_id"] for item in items],
        "orderId": payload.orderId,
        "pageUrl": page_url,
        "fbp": payload.fbp.strip(),
        "fbc": payload.fbc.strip(),
    }

    asyncio.create_task(
        dispatch_capi_event(
            event_name="CompletePayment",
            event_id=event_id,
            payload=capi_payload,
            ip=ip,
            user_agent=user_agent,
        )
    )
    asyncio.create_task(
        dispatch_meta_capi_event(
            event_name="CompletePayment",
            event_id=event_id,
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

    pool = await get_pool(request)
    async with pool.acquire() as conn:
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
        relay_payload = dict(payload.payload)
        if not relay_payload.get("pageUrl"):
            site_url = os.getenv("SITE_URL", "https://alam-hawa.pages.dev").strip().rstrip("/")
            relay_payload["pageUrl"] = site_url + "/"
        asyncio.create_task(
            dispatch_capi_event(
                event_name=payload.eventName,
                event_id=payload.eventId,
                payload=relay_payload,
                ip=ip,
                user_agent=user_agent,
            )
        )
        asyncio.create_task(
            dispatch_meta_capi_event(
                event_name=payload.eventName,
                event_id=payload.eventId,
                payload=relay_payload,
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

    pool = await get_pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch("select * from orders order by created_at desc limit 200")

    result = []
    for row in rows:
        order = dict(row)
        order["created_at"] = order["created_at"].isoformat()
        order["items"] = json.loads(order["items"]) if isinstance(order["items"], str) else order["items"]
        result.append(order)
    return result
