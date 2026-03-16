# GADZILLA Backend

Django REST API for the GADZILLA frontend (Next.js).

## Setup

```bash
cd gadzilla-backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

API: `http://127.0.0.1:8000/`

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/products/` | no | List products. Query: `?category=` (supports comma-separated), `?featured=true`, `?hot_deals=true` |
| GET | `/api/products/<uuid:id>/` | no | Product detail |
| GET | `/api/products/<uuid:id>/related/` | no | Related products |
| GET | `/api/categories/` | no | Categories for nav/FeaturedProducts |
| GET | `/api/wishlist/` | JWT | List wishlist |
| POST | `/api/wishlist/add/` | JWT | Body: `{"product_id": "uuid"}` |
| POST | `/api/wishlist/remove/<uuid:product_id>/` | JWT | Remove from wishlist |
| GET | `/api/cart/` | session | Get cart |
| POST | `/api/cart/add/` | session | Body: `{"product_id": "uuid", "quantity": 1, "size": ""}` |
| PATCH | `/api/cart/items/<id>/update/` | session | Body: `{"quantity": 2}` |
| POST | `/api/cart/items/<id>/remove/` | session | Remove item |
| POST | `/api/orders/` | no | Create order from cart. Body: `{"email","shipping_name","shipping_address"}` |
| GET | `/api/orders/my/` | JWT | My orders |
| GET | `/api/orders/<order_number>/?email=` | no | Order detail (track). Order number is sequential, e.g. `00000001`. Guests: `?email=...` required |
| POST | `/api/contact/` | no | Body: `{"name","email","message"}` |
| POST | `/api/auth/token/` | no | JWT: Body `{"username","password"}` |
| POST | `/api/auth/token/refresh/` | no | Body `{"refresh": "..."}` |

## Product shape (for frontend)

```json
{
  "id": "uuid",
  "name": "string",
  "brand": "string",
  "price": "99.00",
  "originalPrice": "129.00",
  "image": "http://.../media/products/...",
  "images": ["http://..."],
  "badge": "sale" | "new" | "hot",
  "category": "gadgets" | "accessories" | "audio" | "wearables",
  "description": "string",
  "availableSizes": ["XS","S","M","L","XL","XXL"]
}
```

## Auth

- **JWT**: use `/api/auth/token/` with Django `username`/`password`. Frontend: `Authorization: Bearer <access>`.
- **Session**: cart works with session (and optionally JWT for logged-in users).

For Google sign-in, you can later add a custom endpoint that accepts a Google ID token, creates/gets a user, and returns JWT.

## Admin

`/admin/` – manage products, categories, orders, wishlists, cart, contact. Create products and categories after `createsuperuser`.
