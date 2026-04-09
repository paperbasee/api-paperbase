from django.urls import path

from .views import (
    InitiatePaymentView,
    PaymentConfigView,
    PendingPaymentStatusView,
    PlanListView,
    SubmitTransactionView,
)

urlpatterns = [
    path("plans/", PlanListView.as_view(), name="billing_plan_list"),
    path("payment/config/", PaymentConfigView.as_view(), name="billing_payment_config"),
    path("payment/initiate/", InitiatePaymentView.as_view(), name="billing_payment_initiate"),
    path("payment/submit/", SubmitTransactionView.as_view(), name="billing_payment_submit"),
    path("payment/pending/", PendingPaymentStatusView.as_view(), name="billing_payment_pending"),
]
