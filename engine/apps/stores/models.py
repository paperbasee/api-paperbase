from django.conf import settings
from django.db import models

from engine.core.ids import generate_public_id


class Store(models.Model):
    """Tenant store for the BaaS platform. Includes branding for dashboard and storefront."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier used in APIs and URLs (e.g. str_xxx).",
    )
    name = models.CharField(max_length=255)
    store_type = models.CharField(
        max_length=60,
        blank=True,
        help_text="Store type/category (e.g. Fashion, Retail, E-commerce). Max 4 words.",
    )
    domain = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Full domain or host used to route requests to this store. Set via Settings > Networking.",
    )
    is_active = models.BooleanField(default=True)
    # Owner info (always stored with the store)
    owner_name = models.CharField(
        max_length=255,
        help_text="Full name of the store owner.",
    )
    owner_email = models.EmailField(
        help_text="Email address of the store owner.",
    )
    # Branding (dashboard sidebar, storefront, invoices)
    logo = models.ImageField(upload_to="stores/logos/", blank=True, null=True)
    currency = models.CharField(max_length=8, default="BDT")
    currency_symbol = models.CharField(max_length=10, default="৳", blank=True)
    # Store info (for storefront, invoices, emails)
    contact_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    brand_showcase = models.JSONField(
        blank=True,
        default=list,
        help_text="Homepage brand cards: name, slug, image_url, redirect_url, brand_type, order, is_active",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["domain"]),
            models.Index(fields=["is_active", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("store")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name}" + (f" ({self.domain})" if self.domain else "")


class StoreSettings(models.Model):
    """Per-store configuration and feature flags."""

    store = models.OneToOneField(
        Store,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    modules_enabled = models.JSONField(
        default=dict,
        blank=True,
        help_text="Feature/module flags for this store (e.g. products, orders, reviews).",
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text="Default low-stock alert threshold for inventory.",
    )
    extra_field_schema = models.JSONField(
        blank=True,
        default=list,
        help_text="Extra field definitions for product, customer, order: [{id, entityType, name, fieldType, required, order, options}]",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Store settings"

    def __str__(self) -> str:
        return f"Settings for {self.store}"


class StoreMembership(models.Model):
    """Association between a user and a store, with a role."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        STAFF = "staff", "Staff"

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier used in APIs and URLs (e.g. mbr_xxx).",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_memberships",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "store"],
                name="uniq_store_membership_user_store",
            ),
        ]
        indexes = [
            models.Index(fields=["store", "role"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("mbr")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} @ {self.store} ({self.get_role_display()})"


class StoreDeletionJob(models.Model):
    """
    Track irreversible store deletion progress for the initiating user.

    IMPORTANT: this model intentionally stores store identifiers as snapshots
    (instead of FKs) so the job remains queryable even after the store is hard-deleted.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. dlj_xxx).",
    )

    # Step strings returned to the frontend (kept stable for UI).
    STEP_REMOVING_ORDERS = "Removing orders..."
    STEP_CLEARING_CUSTOMERS = "Clearing customer data..."
    STEP_DELETING_PRODUCTS = "Deleting products..."
    STEP_DELETING_ANALYTICS = "Deleting analytics..."
    STEP_FINALIZING = "Finalizing..."

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_deletion_jobs",
    )

    store_public_id_snapshot = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Store public_id snapshot taken at deletion request time.",
    )
    store_id_snapshot = models.BigIntegerField(
        db_index=True,
        help_text="Store primary key snapshot taken at deletion request time.",
    )

    celery_task_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    current_step = models.CharField(max_length=120, blank=True, default="")

    redirect_route = models.CharField(
        max_length=64,
        default="/onboarding",
        help_text="Frontend route to redirect after deletion is finalized.",
    )
    next_store_public_id = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        help_text="Optional next active store public_id for the issuing JWTs.",
    )
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("storedeletionjob")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"StoreDeletionJob({self.store_public_id_snapshot})[{self.status}]"

