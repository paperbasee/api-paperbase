# E-commerce Engine (Django REST API)

Reusable, API-first Django backend for e-commerce. Use it as a plug-and-play backend for any online store: clone, run migrations, connect your frontend.

## Quick start

```bash
cd core
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # Edit .env (defaults to config.settings.development)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

API base: `http://127.0.0.1:8000/api/v1/`

## Seed products

Two built-in product seed commands are available:

- `seed_products`: clears existing products for the selected store and seeds a large demo catalog.
- `seed_apparel_demo`: seeds demo apparel products with variants (shirt + pant).

Run from `backend/`:

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
      cart/                # Cart (guest + user)
      wishlist/            # Wishlist
      orders/              # Orders and order lifecycle
      payments/            # Payment methods and transactions (gateway-ready)
      shipping/            # Shipping zones, methods, rates
      coupons/             # (Reserved for promotions)
      reviews/             # Product reviews and ratings
      notifications/       # Banners and system notifications
      support/             # Support tickets (public submit + admin CRUD)
  engine.apps.analytics/              # Optional Meta Conversions API
```

## API overview (all under `/api/v1/`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| **Auth** |
| POST | `/api/v1/auth/token/` | no | JWT: `{"username","password"}` |
| POST | `/api/v1/auth/token/refresh/` | no | `{"refresh": "..."}` |
| **Products & catalog** |
| GET | `/api/v1/products/` | no | List products. `?category=`, `?brand=`, `?featured=true`, `?hot_deals=true` |
| GET | `/api/v1/products/<id_or_slug>/` | no | Product detail |
| GET | `/api/v1/products/<id>/related/` | no | Related products |
| GET | `/api/v1/categories/` | no | Category tree (tenant-scoped) |
| GET | `/api/v1/banners/` | no | Active store banners (tenant-scoped) |
| **Cart & wishlist** |
| GET | `/api/v1/cart/` | session | Get cart |
| POST | `/api/v1/cart/add/` | session | `{"product_id": "uuid", "quantity": 1, "size": ""}` |
| PATCH | `/api/v1/cart/items/<id>/update/` | session | `{"quantity": 2}` |
| POST | `/api/v1/cart/items/<id>/remove/` | session | Remove item |
| GET | `/api/v1/wishlist/` | session/JWT | List wishlist |
| POST | `/api/v1/wishlist/add/` | session/JWT | `{"product_id": "uuid"}` |
| POST | `/api/v1/wishlist/remove/<uuid:product_id>/` | session/JWT | Remove |
| **Orders** |
| POST | `/api/v1/orders/` | no | Create order from cart |
| GET | `/api/v1/orders/my/` | JWT | My orders |
| GET | `/api/v1/orders/<id>/` | no | Order detail (guests: `?email=...`) |
| **Payments** |
| GET | `/api/v1/payments/methods/` | no | List payment methods |
| POST | `/api/v1/payments/initiate/` | no | Placeholder – plug in Stripe/Razorpay etc. |
| **Shipping** |
| GET | `/api/v1/shipping/options/?country=US&order_total=99` | no | Shipping options and prices |
| **Reviews** |
| GET | `/api/v1/reviews/?product_public_id=<public_id>` | no | Approved reviews for product |
| POST | `/api/v1/reviews/create/` | JWT | Create review |
| GET | `/api/v1/reviews/summary/?product_public_id=<public_id>` | no | Rating summary |
| **Customers** |
| GET / PATCH | `/api/v1/customers/me/` | JWT | Profile |
| GET / POST | `/api/v1/customers/addresses/` | JWT | Addresses |
| GET / PUT / DELETE | `/api/v1/customers/addresses/<id>/` | JWT | Address detail |
| **Other** |
| GET | `/api/v1/notifications/active/` | no | Active banner notifications |
| POST | `/api/v1/support/tickets/` | no | Submit support ticket (tenant host / store context) |

**Admin API** (staff only): `/api/v1/admin/` – stats (`support_tickets`, `supportTickets` in analytics series), analytics, branding, CRUD including `support-tickets/`, products, orders, cart, wishlist, inventory, notifications, etc.

## Environment variables

Use explicit settings modules:

- Development: `DJANGO_SETTINGS_MODULE=config.settings.development`
- Production: `DJANGO_SETTINGS_MODULE=config.settings.production`

See `.env.example` for the full list. Production requires at least:

- `SECRET_KEY`
- `ALLOWED_HOSTS`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `CHANNEL_LAYER_REDIS_URL`
- `CACHE_REDIS_URL`
- `CELERY_BROKER_URL`

## Auth

- **JWT**: `POST /api/v1/auth/token/` with `username` and `password`. Use header: `Authorization: Bearer <access_token>`.
- **Session**: Cart and wishlist support anonymous sessions; optional JWT for logged-in users.

## Using as a template

1. Clone the repository.
2. Create and activate a virtualenv; install dependencies from `requirements.txt`.
3. Copy `.env.example` to `.env` (development profile works out of the box with sqlite).
4. Run `python manage.py migrate` and `python manage.py createsuperuser`.
5. Optionally configure store branding (logo, name, currency) in Django admin (Store model) and R2/Meta in `.env`.
6. Connect any frontend to the `/api/v1/` endpoints.

To add a payment gateway, implement the flow in `engine.apps.payments` (create `Payment`/`Transaction`, call gateway, expose webhook). Shipping rules are configured via admin (zones, methods, rates).

## Admin

Django admin at `/admin/` (or `ADMIN_URL_PATH`). Manage products, categories, orders, inventory, payments, shipping, reviews, customers, and notifications after creating a superuser.
