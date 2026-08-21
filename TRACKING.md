# Basharti tracking — environment variables

## Meta Pixel + Conversions API (CAPI)

| Variable | Required | Description |
|----------|----------|-------------|
| `META_PIXEL_ID` | Yes (for Meta) | Dataset / Pixel ID, e.g. `27899734523051764` |
| `META_CAPI_ACCESS_TOKEN` | Yes (for CAPI) | From Events Manager → Pixel → Settings → Conversions API → Generate access token |
| `META_CAPI_TEST_EVENT_CODE` | No | While testing — paste from Events Manager → Test Events → server instructions. **Unset in production.** |
| `META_GRAPH_API_VERSION` | No | Defaults to `v19.0` |

## TikTok Pixel + Events API

| Variable | Required | Description |
|----------|----------|-------------|
| `TIKTOK_PIXEL_ID` | Yes (for TikTok) | TikTok Pixel ID |
| `TIKTOK_ACCESS_TOKEN` | Yes (for CAPI) | Events API access token |

## Shared

| Variable | Description |
|----------|-------------|
| `SITE_URL` | e.g. `https://bacharati.store` — used as `event_source_url` on Purchase |
| `TRACKING_LOG_CAPI` | `true` (default) — log CAPI send/result lines |

## Testing Meta CAPI

1. Set `META_CAPI_TEST_EVENT_CODE` in Easypanel (backend env).
2. Redeploy backend.
3. Place a test order on the store.
4. Open Meta Events Manager → Test Events — confirm `Purchase` with `action_source: website`.
5. Remove `META_CAPI_TEST_EVENT_CODE` for live traffic.

## Deduplication

Each order uses one `eventId` (UUID) shared by:

- Browser: `fbq('track', 'Purchase', props, { eventID: eventId })`
- Server: Meta CAPI `event_id` field on `/api/orders/complete`

Meta deduplicates browser + server events when the IDs match.
