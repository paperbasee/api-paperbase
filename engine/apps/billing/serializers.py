"""Billing serializers for public plan listing and manual-payment checkout flow."""

from rest_framework import serializers

from .models import Payment, Plan


class PlanSerializer(serializers.ModelSerializer):
    """Read-only plan representation for the public listing endpoint."""

    class Meta:
        model = Plan
        fields = [
            "public_id",
            "name",
            "price",
            "billing_cycle",
            "features",
            "is_default",
        ]
        read_only_fields = fields


class InitiatePaymentSerializer(serializers.Serializer):
    """Validates plan selection; blocks only when a pending payment already has a submitted TXN."""

    plan_public_id = serializers.CharField(max_length=32)

    def validate_plan_public_id(self, value):
        try:
            plan = Plan.objects.get(public_id=value, is_active=True)
        except Plan.DoesNotExist:
            raise serializers.ValidationError("Plan not found or inactive.")
        self._plan = plan
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        pending = (
            Payment.objects.filter(user=user, status=Payment.Status.PENDING)
            .order_by("-created_at")
            .first()
        )
        if pending and (pending.transaction_id or "").strip():
            raise serializers.ValidationError(
                "You already submitted a transaction ID for this payment. "
                "Please wait for review or contact support."
            )
        return attrs

    def get_plan(self):
        return self._plan


class SubmitTransactionSerializer(serializers.Serializer):
    """Validates and submits a transaction ID for an existing pending payment."""

    transaction_id = serializers.CharField(max_length=255)
    sender_number = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")

    def validate_transaction_id(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Transaction ID is required.")
        # Global uniqueness — provider TXN IDs must not be reused across any payment.
        if Payment.objects.filter(transaction_id=value).exists():
            raise serializers.ValidationError(
                "This transaction ID has already been used."
            )
        return value


class PendingPaymentSerializer(serializers.ModelSerializer):
    """Read-only summary of a pending payment, including plan details."""

    plan = PlanSerializer(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "public_id",
            "amount",
            "currency",
            "status",
            "provider",
            "plan",
            "transaction_id",
            "created_at",
        ]
        read_only_fields = fields
