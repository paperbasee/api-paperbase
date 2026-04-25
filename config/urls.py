from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

import inngest.django
from config.inngest import inngest_client
from config import inngest_functions

from config.qstash_views import qstash_inventory_sync, qstash_base_backup

from config.health_views import HealthCheckView
from engine.core.storefront_search_views import StorefrontSearchView


admin.site.site_header = "Paperbase"
admin.site.site_title = "Paperbase"
admin.site.index_title = "Paperbase admin"


def api_home(_request):
    return HttpResponse("home of paperbase API", content_type="text/plain")


def not_found(_request, exception):
    return HttpResponse("Not Found", content_type="text/plain")


from engine.apps.products.urls import category_urlpatterns
# API v1: all public and admin endpoints under /api/v1/
api_v1_patterns = [
    path('auth/', include('engine.apps.accounts.urls')),
    path('admin/', include('config.admin_urls')),
    path('settings/network/', include('engine.apps.stores.network_urls')),
    path('store/', include('engine.apps.stores.urls')),
    path('fraud-check/', include('engine.apps.fraud_check.urls')),
    path('products/', include('engine.apps.products.urls')),
    path('catalog/', include('engine.apps.products.catalog_urls')),
    path('categories/', include(category_urlpatterns)),
    path('banners/', include('engine.apps.banners.urls')),
    path('blogs/', include('engine.apps.blogs.urls')),
    path('orders/', include('engine.apps.orders.urls')),
    path('shipping/', include('engine.apps.shipping.urls')),
    path('pricing/', include('engine.apps.orders.pricing_urls')),
    path('customers/', include('engine.apps.customers.urls')),
    path('notifications/', include('engine.apps.notifications.urls')),
    path('system-notifications/', include('engine.apps.notifications.system_urls')),
    path('support/', include('engine.apps.support.urls')),
    path('search/', StorefrontSearchView.as_view(), name='storefront-search'),
    path('billing/', include('engine.apps.billing.urls')),
]

urlpatterns = [
    path("", api_home),
    path("health", HealthCheckView.as_view()),
    path("tracking/", include("engine.apps.tracking.urls")),
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
    path("webhooks/qstash/inventory-sync/", qstash_inventory_sync, name="qstash-inventory-sync"),
    path("webhooks/qstash/base-backup/", qstash_base_backup, name="qstash-base-backup"),
]


urlpatterns += [
    inngest.django.serve(
        inngest_client,
        [
            inngest_functions.purge_expired_trash,
            inngest_functions.cleanup_event_logs,
            inngest_functions.cleanup_order_exports,
            inngest_functions.backup_table_prune,
        ],
        serve_path="api/inngest",
    )
]

handler404 = "config.urls.not_found"
