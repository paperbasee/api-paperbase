from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from engine.apps.products.urls import (
    navbar_category_urlpatterns,
    category_urlpatterns,
    brands_urlpatterns,
    brand_showcase_urlpatterns,
)

# API v1: all public and admin endpoints under /api/v1/
api_v1_patterns = [
    path('auth/', include('engine.apps.accounts.urls')),
    path('admin/', include('config.admin_urls')),
    path('stores/', include('engine.apps.stores.urls')),
    path('products/', include('engine.apps.products.urls')),
    path('categories/', include(category_urlpatterns)),
    path('navbar-categories/', include(navbar_category_urlpatterns)),
    path('brands/', include(brands_urlpatterns)),
    path('brand-showcase/', include(brand_showcase_urlpatterns)),
    path('cart/', include('engine.apps.cart.urls')),
    path('wishlist/', include('engine.apps.wishlist.urls')),
    path('orders/', include('engine.apps.orders.urls')),
    path('shipping/', include('engine.apps.shipping.urls')),
    path('reviews/', include('engine.apps.reviews.urls')),
    path('customers/', include('engine.apps.customers.urls')),
    path('notifications/', include('engine.apps.notifications.urls')),
    # Backwards-compat alias (old name) + preferred support namespace.
    path('contact/', include('engine.apps.support.urls')),
    path('support/', include('engine.apps.support.urls')),
]

urlpatterns = [
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
