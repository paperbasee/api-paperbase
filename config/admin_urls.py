from django.urls import path, include
from rest_framework.routers import DefaultRouter

from engine.apps.orders.admin_views import AdminOrderViewSet
from engine.apps.orders.order_export_views import (
    OrderExportCreateView,
    OrderExportDownloadView,
    OrderExportStatusView,
)
from engine.apps.products.admin_views import (
    AdminProductViewSet,
    AdminProductImageViewSet,
    AdminCategoryViewSet,
    AdminProductVariantViewSet,
    AdminProductAttributeViewSet,
    AdminProductAttributeValueViewSet,
)
from engine.apps.notifications.admin_views import AdminNotificationViewSet, AdminStaffNotificationViewSet
from engine.apps.support.admin_views import AdminSupportTicketViewSet
from engine.core.admin_views import AdminActivityLogViewSet
from engine.core.admin_trash_views import AdminTrashViewSet
from engine.apps.inventory.admin_views import AdminInventoryViewSet, AdminStockMovementViewSet
from engine.apps.banners.admin_views import AdminBannerViewSet
from engine.apps.blogs.admin_views import (
    AdminBlogTagViewSet,
    AdminBlogViewSet,
)
from engine.apps.customers.admin_views import AdminCustomerViewSet
from engine.apps.shipping.admin_views import (
    AdminShippingZoneViewSet,
    AdminShippingMethodViewSet,
    AdminShippingRateViewSet,
)
from engine.apps.couriers.admin_views import AdminCourierViewSet
from engine.apps.marketing_integrations.admin_views import AdminMarketingIntegrationViewSet

from engine.apps.basic_analytics.views import BasicAnalyticsOverviewView

from .admin_api import (
    DashboardStatsView,
    BrandingView,
)
from .admin_notifications_summary import AdminNotificationsSummaryView
from engine.core.search_views import UnifiedSearchView

router = DefaultRouter()
router.register(r'orders', AdminOrderViewSet, basename='admin-orders')
router.register(r'products', AdminProductViewSet, basename='admin-products')
router.register(r'product-images', AdminProductImageViewSet, basename='admin-product-images')
router.register(r'product-variants', AdminProductVariantViewSet, basename='admin-product-variants')
router.register(r'product-attributes', AdminProductAttributeViewSet, basename='admin-product-attributes')
router.register(
    r'product-attribute-values',
    AdminProductAttributeValueViewSet,
    basename='admin-product-attribute-values',
)
router.register(r'categories', AdminCategoryViewSet, basename='admin-categories')
router.register(r'notifications', AdminNotificationViewSet, basename='admin-notifications')
router.register(r'system-notifications', AdminStaffNotificationViewSet, basename='admin-system-notifications')
router.register(r'support-tickets', AdminSupportTicketViewSet, basename='admin-support-tickets')
router.register(r'activities', AdminActivityLogViewSet, basename='admin-activities')
router.register(r'trash', AdminTrashViewSet, basename='admin-trash')
router.register(r'inventory', AdminInventoryViewSet, basename='admin-inventory')
router.register(r'stock-movements', AdminStockMovementViewSet, basename='admin-stock-movements')
router.register(r'banners', AdminBannerViewSet, basename='admin-banners')
router.register(r'blogs', AdminBlogViewSet, basename='admin-blogs')
router.register(r'blog-tags', AdminBlogTagViewSet, basename='admin-blog-tags')
router.register(r'customers', AdminCustomerViewSet, basename='admin-customers')
router.register(r'shipping-zones', AdminShippingZoneViewSet, basename='admin-shipping-zones')
router.register(r'shipping-methods', AdminShippingMethodViewSet, basename='admin-shipping-methods')
router.register(r'shipping-rates', AdminShippingRateViewSet, basename='admin-shipping-rates')
router.register(r'couriers', AdminCourierViewSet, basename='admin-couriers')
router.register(r'marketing-integrations', AdminMarketingIntegrationViewSet, basename='admin-marketing-integrations')

urlpatterns = [
    path("orders/export/", OrderExportCreateView.as_view(), name="admin-order-export-create"),
    path(
        "orders/export/<uuid:job_id>/",
        OrderExportStatusView.as_view(),
        name="admin-order-export-status",
    ),
    path(
        "orders/export/<uuid:job_id>/download/",
        OrderExportDownloadView.as_view(),
        name="admin-order-export-download",
    ),
    path(
        "search/",
        UnifiedSearchView.as_view(),
        name="admin-unified-search",
    ),
    path(
        "notifications/summary/",
        AdminNotificationsSummaryView.as_view(),
        name="admin-notifications-summary",
    ),
    path('stats/', DashboardStatsView.as_view(), name='admin-dashboard-stats'),
    path(
        'basic-analytics/overview/',
        BasicAnalyticsOverviewView.as_view(),
        name='admin-basic-analytics-overview',
    ),
    path('branding/', BrandingView.as_view(), name='admin-branding'),
    path('', include(router.urls)),
]
