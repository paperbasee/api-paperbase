## Meta dedup verification checklist (Paperbase)

### Prereqs
- **Meta Pixel** is installed on the storefront and `fbq` is available.
- The storefront loads **`/static/tracker.js`**.
- `tracker.js` is configured with the store’s **publishable API key** (`ak_pk_...`) via `tracker.init({ apiKey: ... })` or a global (see file).
- A store has an active **Facebook** `MarketingIntegration` configured (pixel_id + access token).

### Validate Django ingestion (multi-tenant isolation)
- **Wrong API key**:
  - Call `POST /tracking/event` with `Authorization: Bearer ak_pk_invalid`
  - Expect **401**
- **Correct API key**:
  - Call `POST /tracking/event` with a real `ak_pk_...` for StoreA
  - Expect `{"status":"queued"}` and **200**
- **Cross-tenant protection**:
  - Use StoreA key but configure Meta integration only for StoreB
  - Expect DB log entry for StoreA: `tracking` app, `no_integration` (skipped)

### Validate dedup (Pixel + CAPI must share event_id)
For each event below, confirm **the same `event_id`** is used in:
- Browser Pixel call: `fbq('track', ..., { eventID: event_id })`
- CAPI payload: `event_id`

Events to verify:
- `PageView`: page load after `tracker.js` loads
- `ViewContent`: product detail page view
- `AddToCart`: add product to cart action
- `InitiateCheckout`: checkout start action
- `Purchase`: order created action

### Validate in Meta Events Manager
For each of the five events, confirm:
- **Browser event** exists
- **Server event** exists
- Event shows **deduplicated/grouped** (Pixel + CAPI)

### Operational checks (Celery)
- Temporarily stop the worker and trigger events:
  - Django should still return `{"status":"queued"}` immediately
  - Events should later be delivered when worker resumes
- Force a network failure to Meta:
  - Confirm Celery retries (max 3) and logs failures with `store_public_id`, `pixel_id`, `event_name`, `event_id`

