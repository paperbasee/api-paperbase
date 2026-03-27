from django.urls import path

from .views import CouponApplyView, PricingBreakdownView

urlpatterns = [
    path("apply/", CouponApplyView.as_view(), name="coupon-apply"),
    path("pricing-breakdown/", PricingBreakdownView.as_view(), name="coupon-pricing-breakdown"),
]
