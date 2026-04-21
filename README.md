# E-commerce Engine (Django REST API)

Reusable, API-first Django backend for e-commerce. Use it as a plug-and-play backend for any online store: clone, run migrations, connect your frontend.

## Quick start

From the **repository root** (same folder as `manage.py` and this `README.md`):

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # Edit .env; defaults match config.settings.runtime (DEBUG=true, sqlite)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### Docker (same image as production)

From the repository root:

```bash
docker compose up --build
```

Secrets and provider keys (e.g. `RESEND_API_KEY`) live in **`.env`** at the repository root: Compose passes them into the `web` container via `env_file` (the file is not baked into the image). Values under `environment` in `docker-compose.yml` still override `.env` for the keys listed there (such as `DATABASE_URL`).

The **web** image uses [`entrypoint.sh`](entrypoint.sh): wait for Postgres (when `DATABASE_URL` is Postgres), then `migrate`, then `collectstatic`, then Gunicorn. The Dockerfile does not run migrations or collectstatic at build time.

The stack runs Django (`DEBUG=true`), Postgres, and Redis with the same environment variable **names** you use in production; only values change per platform. The web process listens on `PORT` (set to `8000` in `docker-compose.yml`). Override any variable with a root `.env` file and `env_file` on the `web` service if you prefer not to inline defaults in Compose.

API base: `http://127.0.0.1:8000/api/v1/`

### CI (GitHub Actions)

On push to **`master`** (and manual `workflow_dispatch`), [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) uses **Docker Buildx** and **`docker/build-push-action`** with **GHA layer cache** to build and push to **GHCR** (`ghcr.io/<owner>/<repo>:latest` and `:sha`, owner/repo lowercased for the registry). Configure repository secret **`GHCR_TOKEN`** (PAT with `write:packages` for `ghcr.io`). The workflow does **not** deploy anywhere, run migrations, or run `collectstatic` in CI—those run inside the container [`entrypoint.sh`](entrypoint.sh) at runtime.

Deploy on your host or any provider that runs images from a registry (AWS, DigitalOcean, Render, etc.) by pulling the built image or rebuilding from this repo and setting the same environment variables; no vendor-specific CLI is required in this repository.

## Seed products

Two built-in product seed commands are available:

- `seed_products`: clears existing products for the selected store and seeds a large demo catalog.
- `seed_apparel_demo`: seeds demo apparel products with variants (shirt + pant).

From the repository root:

```bash
source venv/bin/activate
python manage.py migrate
python manage.py seed_products
```

For apparel demo:

```bash
source venv/bin/activate
python manage.py seed_apparel_demo
```

Useful options:

```bash
# Seed a specific active store by internal store PK
python manage.py seed_apparel_demo --store-id 4

# Re-create demo apparel products (remove old demo rows first)
python manage.py seed_apparel_demo --force
```

Notes:
- Seed payload files live in `seeds/products/`.
- Create at least one active store first, or seed commands will exit.
- `seed_products` deletes current products for the target store before reseeding.

## Project structure

```
core/
  config/                 # Django project (settings, root URLs)
  engine/
    core/                  # Activity log, shared utilities
    apps/
      accounts/            # Auth (JWT token endpoints)
      customers/           # Customer profile and addresses
      products/            # Products, variants, attributes, images
      categories/          # (Reserved for category tree)
      inventory/           # Stock and stock movements
      wishlist/            # Migration history only (wishlist removed)
      orders/              # Orders and order lifecycle
      payments/            # Payment methods and transactions (gateway-ready)
      shipping/            # Shipping zones, methods, rates
      notifications/       # Banners and system notifications
      support/             # Support tickets (public submit + admin CRUD)
  engine.apps.basic_analytics/        # Home dashboard stats snapshots + overview API
```

### Database: upgrading from the removed `analytics` app

`basic_analytics.0001_initial` creates `analytics_storedashboardstatssnapshot` only if the table is missing, so you can run `python manage.py migrate` on both new databases and databases that already had this table from the old `analytics` app.

If your `django_migrations` table still lists migrations for the removed `analytics` app, delete those rows to avoid confusion (the app no longer exists in the codebase).

## API overview (all under `/api/v1/`)

Storefront catalog, checkout, and public content endpoints require the **publishable API key** (`Authorization: Bearer ak_pk_…`) unless listed as exempt below.

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| **Auth** |
| POST | `/api/v1/auth/token/` | no | JWT: `{"username","password"}` |
| POST | `/api/v1/auth/token/refresh/` | no | `{"refresh": "..."}` |
| **Products & catalog** |
| GET | `/api/v1/products/` | API key | List products. `?category=`, `?brand=`, `?search=`, `?price_min=`, `?price_max=`, `?attributes=` |
| GET | `/api/v1/products/<id_or_slug>/` | API key | Product detail |
| GET | `/api/v1/products/<id>/related/` | API key | Related products |
| GET | `/api/v1/categories/` | API key | Category tree (tenant-scoped) |
| GET | `/api/v1/catalog/filters/` | API key | Filter metadata (categories with `public_id`, `name`, `slug`, attributes, brands, price range) |
| GET | `/api/v1/banners/` | API key | Active store banners (tenant-scoped) |
| GET | `/api/v1/store/public/` | API key | Store branding, `extra_field_schema`, modules, theme, SEO, policy URLs |
| **Orders** |
| POST | `/api/v1/orders/` | API key | Create order: `products[]` with `product_public_id`, `quantity`, optional `variant_public_id`; top-level `shipping_zone_public_id`, optional `shipping_method_public_id`, shipping address fields, `phone` / `email` |
| POST | `/api/v1/pricing/breakdown/` | API key | Full cart pricing (merchandise subtotal + shipping); body includes `items` (`product_public_id`, `quantity`, `variant_public_id`), optional `shipping_zone_public_id` / `shipping_method_public_id` |
| POST | `/api/v1/pricing/preview/` | API key | Single-line pricing preview |
| GET | `/api/v1/orders/<public_id>/` | staff/JWT | Order detail (store-scoped admin) |
| **Payments** |
| GET | `/api/v1/payments/methods/` | no | List payment methods |
| POST | `/api/v1/payments/initiate/` | no | Placeholder – plug in a payment gateway as needed |
| **Shipping** |
| GET | `/api/v1/shipping/options/?zone_public_id=…&order_total=…` | API key | Shipping options for a zone |
| GET | `/api/v1/shipping/zones/` | API key | Zones with cost rules and metadata |
| POST | `/api/v1/shipping/preview/` | API key | Shipping quote for line items |
| **Customers** |
| GET / PATCH | `/api/v1/customers/me/` | JWT | Profile |
| GET / POST | `/api/v1/customers/addresses/` | JWT | Addresses |
| GET / PUT / DELETE | `/api/v1/customers/addresses/<id>/` | JWT | Address detail |
| **Other** |
| GET | `/api/v1/notifications/active/` | API key | Active storefront CTAs |
| GET | `/api/v1/search/?q=…` | API key | Storefront search |
| POST | `/api/v1/support/tickets/` | API key | Submit support ticket |
| **Blog** |
| GET | `/api/v1/blogs/` | API key | Published blog posts (tenant-scoped, feature-gated) |
| GET | `/api/v1/blogs/<public_id>/` | API key | Blog post detail (increments `views`) |

**Admin API** (staff only): `/api/v1/admin/` – stats, `basic-analytics/overview/` (home dashboard series), branding, CRUD including `support-tickets/`, products, orders, inventory, notifications, `blogs/`, `blog-tags/`, etc.

### Storefront JSON contract (breaking conventions)

- **Media:** use `image_url` for absolute URLs (product main image, gallery items, category image, banner image). Gallery rows: `public_id`, `image_url`, `alt`, `order`.
- **Products:** `category_public_id`, `category_slug`, `category_name` (no single `category` slug field). Include `stock_tracking`, `extra_data` (full JSON). Variant SKUs live on each variant only (`variants[].sku`). Variants expose `options` entries with `attribute_public_id`, `attribute_slug`, `attribute_name`, `value_public_id`, `value`. Detail adds `variant_matrix`: keys are attribute **slugs**; each value is `{ "slug", "attribute_public_id", "attribute_name", "values": [{ "value_public_id", "value" }] }`.
- **Categories:** `description`, `image_url`, `is_active`, plus `public_id`, `name`, `slug`, `parent_public_id`, `order`.
- **Banners:** `cta_url` (not `cta_link`), `cta_text`, `image_url`, `start_at`, `end_at`, `created_at`, `updated_at` (ISO 8601 where applicable).
- **Storefront CTAs** (`/notifications/active/`): `cta_url`, `cta_label` (from `link_text`), `cta_text`, `is_active`, `is_currently_active`, `start_at`, `end_at`, `notification_type`, `order`, `created_at`, `updated_at`.
- **Orders (create response):** line items include `variant_sku` and `variant_options` with the same shape as product variant `options`. Order includes `courier_consignment_id`, `sent_to_courier`, `customer_confirmation_sent_at` where applicable.
- **Shipping options:** each option includes `rate_public_id`, `method_public_id`, `method_name`, `method_type`, `method_order`, `zone_public_id`, `zone_name`, `price`, `rate_type`, `min_order_total`, `max_order_total`.
- **Shipping zones list:** each zone includes `zone_public_id`, `name`, `estimated_days`, `is_active`, `created_at`, `updated_at`, `cost_rules`.
## Environment variables

Primary module (12-factor, Docker, local, production):

- `DJANGO_SETTINGS_MODULE=config.settings.runtime`

Legacy shims (same behavior after env defaults):

- `config.settings.development` — forces `DEBUG=true`, then loads `runtime`
- `config.settings.production` — refuses `DEBUG=true`, forces `DEBUG=false`, then loads `runtime`

See `.env.example` for the full list. With `DEBUG=false`, configure at least:

- `SECRET_KEY`
- `ALLOWED_HOSTS` (comma-separated)
- `DATABASE_URL` (Postgres or SQLite URL; `dj-database-url` format)
- `REDIS_URL` (Channels, Django cache, Celery broker/results)
- `STORE_API_KEY_SECRET`, `CSRF_TRUSTED_ORIGINS`

Static and media: by default `DJANGO_STORAGE_BACKEND=filesystem` (WhiteNoise expects collected static files). The **Dockerfile** does not run `collectstatic` at build time; the **entrypoint** runs `collectstatic` at container start so files go to **R2/S3** in production (`DJANGO_STORAGE_BACKEND=s3` and full `R2_*`, or all `R2_*` auto-selected per `config.settings.runtime`). If all `R2_*` variables are set, S3-compatible storage is selected automatically; set `DJANGO_STORAGE_BACKEND=filesystem` to force local static/media even when `R2_*` are present.

### Celery Beat

Beat uses **django-celery-beat** with `DatabaseScheduler` (no `celerybeat-schedule` file on disk), so it runs on ephemeral container filesystems without `PermissionError`.

After installing dependencies, apply migrations so Beat tables exist on the same database as the app:

```bash
DJANGO_SETTINGS_MODULE=config.settings.runtime python manage.py migrate
```

**Separate Beat service:** set the same env vars as the worker (`DJANGO_SETTINGS_MODULE=config.settings.runtime`, `REDIS_URL`, `DATABASE_URL`, etc.). Start command:

```bash
celery -A config beat -l info
```

Do not pass `--schedule` or a schedule file path. Periodic tasks from `CELERY_BEAT_SCHEDULE` sync into the database when Beat starts; you can also view or edit them under Django admin **Periodic tasks**.

## Blog CMS

A tenant-scoped blogging module lives in `engine.apps.blogs`:

- **Models:** `Blog`, `BlogTag`. Every row carries an opaque
  `public_id` (prefixes `blg_`, `btg_`) plus a `store` FK; `(store, slug)`
  is unique per model. `Blog` uses `published_at` (timestamp when the post is
  considered live), plus `is_featured`, `is_public`, `is_deleted` (soft-delete),
  and `views`. There is no `status` or `scheduled_at` column — visibility on the
  storefront is driven by `published_at`, `is_public`, and `is_deleted`.
- **Media:** the featured image reuses the existing
  `engine.core.media_upload_paths` pipeline
  (`tenant_blog_featured_image_upload_to` → `tenants/<store>/blogs/<blog>/…`).
  No new storage, no new endpoints, no inline content image uploads — the
  body is plain text/markdown/HTML and authors paste external URLs for
  inline images.
- **Storefront endpoints:** `GET /api/v1/blogs/` and
  `GET /api/v1/blogs/<public_id>/` return only rows with `published_at` set,
  `published_at` not in the future, `is_public=true`, and `is_deleted=false`,
  scoped to the store resolved from the publishable API key. Detail requests
  increment `views` atomically.
- **Dashboard endpoints:** `GET/POST/PATCH/DELETE /api/v1/admin/blogs/` for CRUD.
  On create and update, the API sets `published_at` to the current time when it
  is still null so new posts become visible without a separate publish step.
  `/api/v1/admin/blog-tags/` provides blog taxonomy. Destroys are soft
  (`is_deleted=True`, media scheduled for deletion). List supports optional
  filters: `q` or `search` (title contains), `tag` (tag `public_id`),
  `published_date` (`today` | `last_7_days` | `last_30_days`, same semantics
  as customer “joined date” filters on `published_at`).
- **Feature toggle:** endpoints (both storefront and admin) require
  `StoreSettings.modules_enabled["blog"] === true`, enforced by
  `engine.core.permissions.IsModuleEnabled` (returns `403` when disabled).
  The dashboard controls this flag from *Apps → Blog*; the frontend hides
  the module UI when the flag is off.
- **Tenant isolation:** every query filters by the active store resolved via
  `get_active_store(request)` / `require_api_key_store(request)`; all
  external identifiers are `public_id` / `*_public_id`
  (enforced by `SafeModelSerializer`).

## Auth

- **JWT**: `POST /api/v1/auth/token/` with `username` and `password`. Use header: `Authorization: Bearer <access_token>`.
- **Storefront**: Use the publishable key (`ak_pk_…`) with `Authorization: Bearer ak_pk_…` for catalog, search, banners, CTAs, shipping quotes, checkout, and support. Dashboard CRUD uses JWT + `X-Store-Public-ID` (or equivalent store resolution) and does not use the publishable key on `/api/v1/admin/…`.

## Using as a template

1. Clone the repository.
2. Create and activate a virtualenv; install dependencies from `requirements.txt`.
3. Copy `.env.example` to `.env` (development profile works out of the box with sqlite).
4. Run `python manage.py migrate` and `python manage.py createsuperuser`.
5. Optionally configure store branding (logo, name, currency) in Django admin (Store model) and R2/Meta in `.env`.
6. Connect any frontend to the `/api/v1/` endpoints.

To add a payment gateway, implement the flow in `engine.apps.payments` (create `Payment`/`Transaction`, call gateway, expose webhook). Shipping rules are configured via admin (zones, methods, rates).

## Admin

Django admin at `/<ADMIN_URL_PATH>` (defaults to `/admin/`). Configure via the `ADMIN_PATH` environment variable (no leading slash).
Manage products, categories, orders, inventory, payments, shipping, customers, and notifications after creating a superuser.
