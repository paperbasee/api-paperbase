from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from engine.core.search_views import UnifiedSearchView

def health(_request):
    return JsonResponse({"status": "ok"})

from engine.apps.products.urls import category_urlpatterns

# API v1: all public and admin endpoints under /api/v1/
api_v1_patterns = [
    path('auth/', include('engine.apps.accounts.urls')),
    path('admin/', include('config.admin_urls')),
    path('settings/network/', include('engine.apps.stores.network_urls')),
    path('stores/', include('engine.apps.stores.urls')),
    path('products/', include('engine.apps.products.urls')),
    path('categories/', include(category_urlpatterns)),
    path('banners/', include('engine.apps.banners.urls')),
    path('cart/', include('engine.apps.cart.urls')),
    path('wishlist/', include('engine.apps.wishlist.urls')),
    path('orders/', include('engine.apps.orders.urls')),
    path('shipping/', include('engine.apps.shipping.urls')),
    path('reviews/', include('engine.apps.reviews.urls')),
    path('customers/', include('engine.apps.customers.urls')),
    path('notifications/', include('engine.apps.notifications.urls')),
    path('system-notifications/', include('engine.apps.notifications.system_urls')),
    path('support/', include('engine.apps.support.urls')),
    path('search/', UnifiedSearchView.as_view(), name='global-search'),
]

urlpatterns = [
    path("health", health),
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
