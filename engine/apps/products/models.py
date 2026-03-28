import uuid
from django.db import models  # type: ignore[import-not-found]

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id
from engine.core.tenant_queryset import TenantAwareManager

try:
    # Import at module level so tooling resolves it consistently.
    from django.core.exceptions import ValidationError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    # Fallback for editors/type-checkers when Django isn't in the active interpreter.
    class ValidationError(Exception):
        pass


class Category(models.Model):
    """
    Hierarchical product categories (self-referencing parent), max 5 levels from root.

    A category can be top-level (parent is null) or a child of another category
    in the same store. Cycles are not allowed.
    """

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier used in APIs and URLs (e.g. cat_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(
        max_length=100,
        help_text="Slug unique per store for this category.",
    )
    description = models.TextField(
        blank=True,
        help_text="Category description for the frontend",
    )
    image = models.ImageField(upload_to="categories/", blank=True, null=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        help_text="Optional parent category; null means top-level.",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order among siblings.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this category is visible on the site",
    )

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "Categories"
        constraints = [
            models.UniqueConstraint(
                fields=["store", "slug"],
                name="uniq_category_store_slug",
            ),
        ]

    objects = TenantAwareManager()

    def clean(self):
        super().clean()
        if not self.store_id:
            return
        from .category_tree import validate_category_parent

        parent = self.parent
        if parent is None and self.parent_id:
            parent = Category.objects.filter(pk=self.parent_id).first()
        validate_category_parent(
            instance_pk=self.pk,
            store_id=self.store_id,
            parent=parent,
        )

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("category")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    """Product model aligned with frontend Product interface. Supports variants and attributes."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        ARCHIVED = 'archived', 'Archived'

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="products",
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Prefixed public identifier for APIs and URLs (e.g. prd_xxx).",
    )
    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=100, blank=True, null=True)
    slug = models.SlugField(max_length=255)
    sku = models.CharField(max_length=100, blank=True, db_index=True, help_text="Stock keeping unit")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        help_text="Leaf or intermediate category this product belongs to.",
    )
    description = models.TextField(blank=True)
    stock = models.PositiveIntegerField(
        default=0,
        help_text="Available stock (used when stock_tracking is True and no variants)"
    )
    stock_tracking = models.BooleanField(
        default=True,
        help_text="When True, stock is tracked (product or variants)"
    )
    is_active = models.BooleanField(default=True)
    extra_data = models.JSONField(
        blank=True,
        default=dict,
        help_text="Dynamic extra fields per extra_field_schema (e.g. color, warranty).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=["store", "slug"],
                name="uniq_product_store_slug",
            ),
        ]

    objects = TenantAwareManager()

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        from .constants import MAX_PRODUCT_IMAGES_TOTAL

        if not self.pk:
            return
        main = 1 if (self.image and getattr(self.image, "name", None)) else 0
        gallery = self.images.count()
        if main + gallery > MAX_PRODUCT_IMAGES_TOTAL:
            raise ValidationError(
                {
                    "image": (
                        f"A product can have at most {MAX_PRODUCT_IMAGES_TOTAL} images in total "
                        "(main image + gallery). Remove gallery images in the inline below or clear "
                        "the main image."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("product")
        # Canonicalize optional brand: store missing/blank as NULL, not empty string.
        if isinstance(self.brand, str):
            self.brand = self.brand.strip() or None
        # Auto-generate slug from name - always update when name changes
        from django.utils.text import slugify

        base_slug = slugify(self.name)

        if not base_slug:
            base_slug = f"product-{self.id}" if self.pk else "product"

        self.slug = base_slug

        queryset = Product.objects.filter(store=self.store)
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)

        counter = 2
        original_slug = self.slug
        while queryset.filter(slug=self.slug).exists():
            self.slug = f"{original_slug}-{counter}"
            counter += 1

        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def category_slug(self):
        return self.category.slug if self.category else None


class ProductImage(models.Model):
    """Additional images for product detail gallery."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. img_xxx).",
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images'
    )
    image = models.ImageField(upload_to='products/gallery/')
    alt = models.CharField(max_length=255, blank=True, help_text="Alt text for accessibility")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def clean(self):
        super().clean()
        from .constants import MAX_PRODUCT_IMAGES_TOTAL

        product = self.product
        if not product.pk:
            return
        main = 1 if (product.image and getattr(product.image, "name", None)) else 0
        others = product.images.exclude(pk=self.pk) if self.pk else product.images.all()
        gcount = others.count()
        if self._state.adding and main + gcount >= MAX_PRODUCT_IMAGES_TOTAL:
            raise ValidationError(
                {
                    "image": (
                        f"Maximum {MAX_PRODUCT_IMAGES_TOTAL} images per product "
                        "(including the main image field on the product)."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("image")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Image for {self.product_id}"


class ProductAttribute(models.Model):
    """Generic attribute type (e.g. Color, Size)."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. atr_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="product_attributes",
        db_index=True,
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(
                fields=["store", "slug"],
                name="uniq_product_attribute_store_slug",
            ),
        ]

    objects = TenantAwareManager()

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("attribute")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProductAttributeValue(models.Model):
    """Specific value for an attribute (e.g. Red, M)."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. atv_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="product_attribute_values",
        db_index=True,
    )
    attribute = models.ForeignKey(
        ProductAttribute,
        on_delete=models.CASCADE,
        related_name='values'
    )
    value = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['attribute', 'order']
        constraints = [
            models.UniqueConstraint(
                fields=["attribute", "value"],
                name="uniq_product_attribute_value_attribute_value",
            ),
            models.UniqueConstraint(
                fields=["store", "attribute", "value"],
                name="uniq_product_attribute_value_store_attribute_value",
            ),
        ]

    objects = TenantAwareManager()

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("attrvalue")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"


class ProductVariant(models.Model):
    """Variant of a product (e.g. size/color combination) with its own SKU and optional price/stock."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. var_xxx).",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants'
    )
    sku = models.CharField(max_length=100, blank=True, db_index=True)
    price_override = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Override product price for this variant"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['product', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=["product", "sku"],
                name="uniq_variant_product_sku",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("variant")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} ({self.sku or self.pk})"

    @property
    def effective_price(self):
        return self.price_override if self.price_override is not None else self.product.price


class ProductVariantAttribute(models.Model):
    """Links a variant to an attribute value (e.g. variant has Color=Red, Size=M)."""
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name='attribute_values'
    )
    attribute_value = models.ForeignKey(
        ProductAttributeValue,
        on_delete=models.CASCADE,
        related_name='variant_links'
    )

    class Meta:
        unique_together = [['variant', 'attribute_value']]
