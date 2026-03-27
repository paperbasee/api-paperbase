from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from engine.apps.coupons.models import BulkDiscount, Coupon, CouponUsage
from engine.apps.products.models import Product


@dataclass(frozen=True)
class CouponQuote:
    coupon: Coupon
    discount_amount: Decimal


@dataclass(frozen=True)
class BulkDiscountQuote:
    rule: Optional[BulkDiscount]
    discount_amount: Decimal


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _resolve_discount_amount(*, coupon: Coupon, subtotal: Decimal) -> Decimal:
    if coupon.discount_type == Coupon.DiscountType.PERCENTAGE:
        amount = (subtotal * coupon.discount_value) / Decimal("100")
    else:
        amount = coupon.discount_value
    amount = _quantize_money(amount)
    if amount < Decimal("0.00"):
        return Decimal("0.00")
    if amount > subtotal:
        return subtotal
    return amount


class CouponValidator:
    @staticmethod
    def validate_for_subtotal(
        *,
        store,
        code: str,
        subtotal: Decimal,
        user=None,
    ) -> CouponQuote:
        normalized_code = (code or "").strip()
        if not normalized_code:
            raise ValidationError({"coupon_code": "Coupon code is required."})
        if subtotal <= Decimal("0.00"):
            raise ValidationError({"coupon_code": "Coupon cannot be applied to empty subtotal."})

        coupon = (
            Coupon.objects.filter(
                store=store,
                code__iexact=normalized_code,
                is_active=True,
            )
            .order_by("-created_at")
            .first()
        )
        if coupon is None:
            raise ValidationError({"coupon_code": "Invalid coupon code."})

        now = timezone.now()
        if coupon.valid_from and now < coupon.valid_from:
            raise ValidationError({"coupon_code": "Coupon is not active yet."})
        if coupon.valid_until and now > coupon.valid_until:
            raise ValidationError({"coupon_code": "Coupon has expired."})
        if coupon.max_uses is not None and coupon.times_used >= coupon.max_uses:
            raise ValidationError({"coupon_code": "Coupon usage limit reached."})
        if coupon.min_order_value is not None and subtotal < coupon.min_order_value:
            raise ValidationError(
                {"coupon_code": f"Minimum order amount is {coupon.min_order_value} for this coupon."}
            )
        if coupon.per_user_max_uses is not None:
            if not getattr(user, "is_authenticated", False):
                raise ValidationError({"coupon_code": "Login is required for this coupon."})
            user_usage_count = CouponUsage.objects.filter(
                store=store,
                coupon=coupon,
                user=user,
                is_reversed=False,
            ).count()
            if user_usage_count >= coupon.per_user_max_uses:
                raise ValidationError({"coupon_code": "Per-user coupon usage limit reached."})

        discount_amount = _resolve_discount_amount(coupon=coupon, subtotal=subtotal)
        if discount_amount <= Decimal("0.00"):
            raise ValidationError({"coupon_code": "Coupon is not applicable for this order."})
        return CouponQuote(coupon=coupon, discount_amount=discount_amount)


class DiscountResolver:
    @staticmethod
    def resolve_bulk_discount_for_product(*, store, product: Product, line_subtotal: Decimal) -> BulkDiscountQuote:
        now = timezone.now()
        qs = BulkDiscount.objects.filter(
            store=store,
            is_active=True,
        ).filter(
            Q(start_date__isnull=True) | Q(start_date__lte=now),
            Q(end_date__isnull=True) | Q(end_date__gte=now),
        )
        category_parent = getattr(product.category, "parent", None)
        rules = list(
            qs.filter(
                Q(target_type=BulkDiscount.TargetType.PRODUCT, product=product)
                | Q(target_type=BulkDiscount.TargetType.SUBCATEGORY, category=product.category)
                | Q(target_type=BulkDiscount.TargetType.CATEGORY, category=category_parent)
            ).order_by("-priority", "-created_at")
        )
        if not rules:
            return BulkDiscountQuote(rule=None, discount_amount=Decimal("0.00"))
        best_rule = rules[0]
        if best_rule.discount_type == BulkDiscount.DiscountType.PERCENTAGE:
            amount = (line_subtotal * best_rule.discount_value) / Decimal("100")
        else:
            amount = best_rule.discount_value
        amount = _quantize_money(amount)
        if amount < Decimal("0.00"):
            amount = Decimal("0.00")
        if amount > line_subtotal:
            amount = line_subtotal
        return BulkDiscountQuote(rule=best_rule, discount_amount=amount)


def validate_coupon_for_subtotal(*, store, code: str, subtotal: Decimal, user=None) -> CouponQuote:
    return CouponValidator.validate_for_subtotal(
        store=store,
        code=code,
        subtotal=subtotal,
        user=user,
    )


def consume_coupon_usage(*, coupon: Coupon, order=None, user=None, email: str = "", phone: str = "") -> None:
    with transaction.atomic():
        locked = Coupon.objects.select_for_update().get(pk=coupon.pk)
        if locked.max_uses is not None and locked.times_used >= locked.max_uses:
            raise ValidationError({"coupon_code": "Coupon usage limit reached."})
        Coupon.objects.filter(pk=locked.pk).update(times_used=F("times_used") + 1)
        if order is not None:
            CouponUsage.objects.get_or_create(
                store=locked.store,
                coupon=locked,
                order=order,
                defaults={
                    "user": user if getattr(user, "is_authenticated", False) else None,
                    "email": (email or "").strip(),
                    "phone": (phone or "").strip(),
                },
            )


def reverse_coupon_usage_for_order(*, order, reason: str) -> None:
    if not order.coupon_id:
        return
    with transaction.atomic():
        locked_coupon = Coupon.objects.select_for_update().get(pk=order.coupon_id)
        usage = (
            CouponUsage.objects.select_for_update()
            .filter(store=order.store, coupon=locked_coupon, order=order)
            .first()
        )
        if usage is None or usage.is_reversed:
            return
        usage.is_reversed = True
        usage.reversed_at = timezone.now()
        usage.reverse_reason = (reason or "")[:20]
        usage.save(update_fields=["is_reversed", "reversed_at", "reverse_reason", "updated_at"])
        if locked_coupon.times_used > 0:
            Coupon.objects.filter(pk=locked_coupon.pk).update(times_used=F("times_used") - 1)


def get_coupon_usage_stats(*, store, coupon: Coupon) -> dict:
    usages = CouponUsage.objects.filter(store=store, coupon=coupon)
    successful = usages.filter(is_reversed=False).count()
    reversed_count = usages.filter(is_reversed=True).count()
    return {
        "successful_uses": successful,
        "reversed_uses": reversed_count,
    }
