from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from products.urls import (
    navbar_category_urlpatterns,
    category_urlpatterns,
    brands_urlpatterns,
    brand_showcase_urlpatterns,
)

urlpatterns = [
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    # Auth (JWT)
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    # Admin API
    path('api/admin/', include('config.admin_urls')),
    # Public API
    path('api/products/', include('products.urls')),
    path('api/navbar-categories/', include(navbar_category_urlpatterns)),
    path('api/categories/', include(category_urlpatterns)),
    path('api/brands/', include(brands_urlpatterns)),
    path('api/brand-showcase/', include(brand_showcase_urlpatterns)),
    path('api/wishlist/', include('wishlist.urls')),
    path('api/cart/', include('cart.urls')),
    path('api/orders/', include('orders.urls')),
    path('api/contact/', include('contact.urls')),
    path('api/notifications/', include('notifications.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
