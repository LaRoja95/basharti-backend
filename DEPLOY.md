# عالم حواء API — نشر على Railway

## 1) إنشاء المشروع

1. ادخلي [railway.app](https://railway.app) وسجّلي الدخول (GitHub).
2. **New Project** → **Deploy from GitHub repo** → اختاري `basharti-backend`  
   أو: **Empty Project** ثم ارفعي الكود بـ Railway CLI.
3. أضيفي قاعدة بيانات: **+ New** → **Database** → **PostgreSQL**.

## 2) متغيرات البيئة (Variables)

في خدمة الـ API (مش الـ Postgres)، حطي:

| المتغير | القيمة |
|---------|--------|
| `DATABASE_URL` | من Postgres → Variables → `DATABASE_URL` (Reference) |
| `CORS_ORIGINS` | `https://alam-hawa.pages.dev` |
| `SITE_URL` | `https://alam-hawa.pages.dev` |
| `ADMIN_TOKEN` | سر طويل عشوائي |

اختياري لاحقاً: `META_CAPI_ACCESS_TOKEN`, `GOOGLE_SHEETS_WEBHOOK_URL`.

## 3) الدومين

في خدمة الـ API → **Settings** → **Networking** → **Generate Domain**.  
انسخي الرابط (مثال: `https://alam-hawa-api.up.railway.app`).

## 4) ربط الفرونت

في `basharti-frontend/config.js`:

```js
API_BASE: "https://YOUR-RAILWAY-DOMAIN.up.railway.app",
```

ثم أعيدي نشر Cloudflare Pages (`alam-hawa`).

## 5) تحقق

```
https://YOUR-RAILWAY-DOMAIN.up.railway.app/health
```

لازم تشوفي `"status":"ok"` و `"dbConnected": true`.

جرّبي طلب تجريبي من المتجر: سلة → تأكيد → صفحة شكراً.

## CLI (اختياري)

```bash
cd basharti-backend
npx @railway/cli login
npx @railway/cli init
npx @railway/cli add --database postgres
npx @railway/cli up
npx @railway/cli domain
```
