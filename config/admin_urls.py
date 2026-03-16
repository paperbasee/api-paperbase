from django.urls import path, include
from rest_framework.routers import DefaultRouter

from orders.admin_views import AdminOrderViewSet
from products.admin_views import (
    AdminProductViewSet,
    AdminProductImageViewSet,
    AdminNavbarCategoryViewSet,
    AdminCategoryViewSet,
    AdminBrandViewSet,
)
from notifications.admin_views import AdminNotificationViewSet
from contact.admin_views import AdminContactSubmissionViewSet
from cart.admin_views import AdminCartViewSet
from wishlist.admin_views import AdminWishlistItemViewSet
from core.admin_views import AdminActivityLogViewSet

from .admin_api import DashboardStatsView, BrandingView, DashboardAnalyticsView

router = DefaultRouter()
router.register(r'orders', AdminOrderViewSet, basename='admin-orders')
router.register(r'products', AdminProductViewSet, basename='admin-products')
router.register(r'product-images', AdminProductImageViewSet, basename='admin-product-images')
router.register(r'navbar-categories', AdminNavbarCategoryViewSet, basename='admin-navbar-categories')
router.register(r'categories', AdminCategoryViewSet, basename='admin-categories')
router.register(r'brands', AdminBrandViewSet, basename='admin-brands')
router.register(r'notifications', AdminNotificationViewSet, basename='admin-notifications')
router.register(r'contacts', AdminContactSubmissionViewSet, basename='admin-contacts')
router.register(r'carts', AdminCartViewSet, basename='admin-carts')
router.register(r'wishlist', AdminWishlistItemViewSet, basename='admin-wishlist')
router.register(r'activities', AdminActivityLogViewSet, basename='admin-activities')

urlpatterns = [
    path('stats/', DashboardStatsView.as_view(), name='admin-dashboard-stats'),
    path('analytics/overview/', DashboardAnalyticsView.as_view(), name='admin-dashboard-analytics'),
    path('branding/', BrandingView.as_view(), name='admin-branding'),
    path('', include(router.urls)),
]
