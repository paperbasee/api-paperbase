"""Keep Product.stock aligned with variant inventory when variants exist."""

from django.db.models import Sum

from .models import Product


def sync_product_stock_from_variants(product_id) -> None:
    """
    Set product.stock to the sum of variant stock_quantity when the product
    has at least one variant. When there are no variants, leave stock unchanged.
    """
    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist:
        return
    if product.variants.count() == 0:
        return
    total = product.variants.aggregate(s=Sum("stock_quantity"))["s"] or 0
    Product.objects.filter(pk=product_id).update(stock=int(total))
