"""Billing API views for the manual-payment checkout flow.

All views use IsVerifiedUser — authenticated + verified email, no subscription required.
This is intentional: users need these endpoints to *acquire* a subscription.
"""

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.core.authz import IsVerifiedUser

from .models import Payment, Plan
from .pricing import plan_charge_amount
from .serializers import (
    InitiatePaymentSerializer,
    PendingPaymentSerializer,
    PlanSerializer,
    SubmitTransactionSerializer,
)


class PlanListView(APIView):
    """GET /api/v1/billing/plans/ — list all active plans."""

    permission_classes = [IsVerifiedUser]

    def get(self, request):
        plans = Plan.objects.filter(is_active=True).order_by("price")
        serializer = PlanSerializer(plans, many=True)
        return Response(serializer.data)


class PaymentConfigView(APIView):
    """GET /api/v1/billing/payment/config/ — return payment receiver details."""

    permission_classes = [IsVerifiedUser]

    def get(self, request):
        return Response(
            {
                "bkash_number": getattr(settings, "BKASH_NUMBER", ""),
                "nagad_number": getattr(settings, "NAGAD_NUMBER", ""),
            }
        )


class InitiatePaymentView(APIView):
    """POST /api/v1/billing/payment/initiate/ — create or retarget a PENDING payment for a plan."""

    permission_classes = [IsVerifiedUser]

    def post(self, request):
        serializer = InitiatePaymentSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        plan = serializer.get_plan()
        expected_amount = plan_charge_amount(plan)
        existing = (
            Payment.objects.filter(user=request.user, status=Payment.Status.PENDING)
            .order_by("-created_at")
            .first()
        )
        if existing and not (existing.transaction_id or "").strip():
            existing.plan = plan
            existing.amount = expected_amount
            existing.currency = "BDT"
            existing.metadata = {}
            existing.save(update_fields=["plan", "amount", "currency", "metadata"])
            return Response(PendingPaymentSerializer(existing).data, status=200)

        payment = Payment.objects.create(
            user=request.user,
            plan=plan,
            subscription=None,
            amount=expected_amount,
            currency="BDT",
            status=Payment.Status.PENDING,
            provider=Payment.Provider.MANUAL,
            transaction_id=None,
            metadata={},
        )
        return Response(PendingPaymentSerializer(payment).data, status=201)


class SubmitTransactionView(APIView):
    """POST /api/v1/billing/payment/submit/ — attach a transaction ID to the pending payment."""

    permission_classes = [IsVerifiedUser]

    def post(self, request):
        pending = (
            Payment.objects.filter(user=request.user, status=Payment.Status.PENDING)
            .select_related("plan")
            .first()
        )
        if not pending:
            return Response(
                {"detail": "No pending payment found. Please select a plan first."},
                status=400,
            )

        serializer = SubmitTransactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        pending.transaction_id = serializer.validated_data["transaction_id"]
        sender = serializer.validated_data.get("sender_number", "").strip()
        if sender:
            pending.metadata = {**pending.metadata, "sender_number": sender}
        pending.save(update_fields=["transaction_id", "metadata"])

        return Response(PendingPaymentSerializer(pending).data)


class PendingPaymentStatusView(APIView):
    """GET /api/v1/billing/payment/pending/ — retrieve the current pending payment for this user."""

    permission_classes = [IsVerifiedUser]

    def get(self, request):
        pending = (
            Payment.objects.filter(user=request.user, status=Payment.Status.PENDING)
            .select_related("plan")
            .first()
        )
        if not pending:
            return Response({"pending": False, "payment": None})
        return Response({"pending": True, "payment": PendingPaymentSerializer(pending).data})
