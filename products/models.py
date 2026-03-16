import uuid
from django.db import models  # type: ignore[import-not-found]

try:
    # Import at module level so tooling resolves it consistently.
    from django.core.exceptions import ValidationError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    # Fallback for editors/type-checkers when Django isn't in the active interpreter.
    class ValidationError(Exception):
        pass


class NavbarCategory(models.Model):
    """
    Top-level navigation categories displayed in the site navbar.

    Examples: Gadgets, Accessories (and any future main categories).
    Managed entirely from the admin panel — no frontend code changes needed
    to add new navbar categories.
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True, help_text="Category description for the frontend")
    image = models.ImageField(upload_to='navbar_categories/', blank=True, null=True)
    order = models.PositiveIntegerField(default=0, help_text="Display order in navigation")
    is_active = models.BooleanField(default=True, help_text="Whether this category is visible on the site")

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Navbar Category'
        verbose_name_plural = 'Navbar Categories'

    def __str__(self):
        return self.name

    def get_subcategories(self):
        """Returns all active subcategories for this navbar category."""
        return self.subcategories.filter(is_active=True).order_by('order', 'name')


class Category(models.Model):
    """
    Subcategories that live under a NavbarCategory.

    Examples: Audio, Wearables (under Gadgets); Chargers, Cables (under Accessories).
    Can be managed from the admin panel without any frontend code changes.
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True, help_text="Category description for the frontend")
    image = models.ImageField(upload_to='categories/', blank=True, null=True)
    navbar_category = models.ForeignKey(
        NavbarCategory,
        on_delete=models.CASCADE,
        related_name='subcategories',
        help_text="The navbar (main) category this subcategory belongs to"
    )
    order = models.PositiveIntegerField(default=0, help_text="Display order in navigation")
    is_active = models.BooleanField(default=True, help_text="Whether this category is visible on the site")

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Categories'

    def __str__(self):
        return f"{self.navbar_category.name} > {self.name}"


class Product(models.Model):
    """Product model aligned with frontend Product interface."""

    class Badge(models.TextChoices):
        SALE = 'sale', 'Sale'
        NEW = 'new', 'New'
        HOT = 'hot', 'Hot'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=100)
    slug = models.SlugField(max_length=255, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    badge = models.CharField(
        max_length=10, choices=Badge.choices, blank=True, null=True
    )
    category = models.ForeignKey(
        NavbarCategory,
        on_delete=models.PROTECT,
        related_name='products',
        help_text="Main navbar category (e.g., Gadgets, Accessories)"
    )
    sub_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subcategory_products',
        help_text="Subcategory (e.g., Audio, Chargers, Power Bank)"
    )
    description = models.TextField(blank=True)
    stock = models.PositiveIntegerField(
        default=0,
        help_text="Available stock quantity for this product"
    )
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def clean(self):
        """
        Ensure sub_category belongs to the selected navbar category.
        """
        super().clean()

        if self.sub_category and self.category:
            if self.sub_category.navbar_category_id != self.category.id:
                raise ValidationError({
                    'sub_category': f'Subcategory must belong to {self.category.name}.'
                })

    def save(self, *args, **kwargs):
        # Auto-generate slug from name - always update when name changes
        from django.utils.text import slugify

        base_slug = slugify(self.name)

        if not base_slug:
            base_slug = f"product-{self.id}" if self.pk else "product"

        self.slug = base_slug

        queryset = Product.objects.all()
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)

        counter = 1
        original_slug = self.slug
        while queryset.filter(slug=self.slug).exists():
            self.slug = f"{original_slug}-{counter}"
            counter += 1

        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def category_slug(self):
        """Returns the navbar category slug for URL generation."""
        return self.category.slug if self.category else None

    @property
    def sub_category_slug(self):
        """Returns the subcategory slug for URL generation."""
        return self.sub_category.slug if self.sub_category else None


class ProductImage(models.Model):
    """Additional images for product detail gallery."""
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images'
    )
    image = models.ImageField(upload_to='products/gallery/')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']


class Brand(models.Model):
    """
    Brand model for showcasing top brands on the homepage.
    Supports image upload and redirect URL for brand cards.
    """

    class BrandType(models.TextChoices):
        ACCESSORIES = 'accessories', 'Accessories'
        GADGETS = 'gadgets', 'Gadgets'

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    image = models.ImageField(
        upload_to='brands/',
        help_text="Brand logo or image to display on the brand card"
    )
    redirect_url = models.URLField(
        max_length=500,
        help_text="URL to redirect users when they click on the brand card"
    )
    brand_type = models.CharField(
        max_length=20,
        choices=BrandType.choices,
        help_text="Determines which section the brand appears in on the homepage"
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order within the brand type section (lower numbers appear first)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this brand is visible on the site"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['brand_type', 'order', 'name']
        verbose_name = 'Brand'
        verbose_name_plural = 'Brands'

    def __str__(self):
        return f"{self.name} ({self.get_brand_type_display()})"
