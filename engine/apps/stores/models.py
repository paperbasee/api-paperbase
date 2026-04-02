import re

from django.conf import settings
from django.db import models

from engine.core.ids import generate_public_id
from engine.core.media_upload_paths import tenant_store_logo_upload_to


class Store(models.Model):
    """Tenant store for the BaaS platform. Includes branding for dashboard and storefront."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier used in APIs and URLs (e.g. str_xxx).",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="URL-safe unique slug (globally unique); may change for branding. Not used in SKUs.",
    )
    code = models.CharField(
        max_length=10,
        unique=True,
        db_index=True,
        help_text="Stable uppercase alphanumeric tenant code for variant SKUs; set once and never changes.",
    )
    store_type = models.CharField(
        max_length=60,
        blank=True,
        help_text="Store type/category (e.g. Fashion, Retail, E-commerce). Max 4 words.",
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
    logo = models.ImageField(upload_to=tenant_store_logo_upload_to, blank=True, null=True)
    currency = models.CharField(max_length=8, default="BDT")
    currency_symbol = models.CharField(max_length=10, default="৳", blank=True)
    # Store info (for storefront, invoices, emails)
    contact_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("store")
        locked_code = None
        if self.pk:
            locked_code = Store.objects.filter(pk=self.pk).values_list("code", flat=True).first()

        from django.utils.text import slugify

        raw = (self.slug or "").strip()
        if raw:
            self.slug = (slugify(raw)[:100] or raw)[:100]
        else:
            base_source = (self.name or "").strip()
            base = slugify(base_source)[:100]
            if not base:
                base = f"store-{self.pk}" if self.pk else "store"
            self.slug = base[:100]
        original = self.slug
        qs = Store.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        counter = 2
        while qs.filter(slug=self.slug).exists():
            suffix = f"-{counter}"
            head_len = max(1, 100 - len(suffix))
            self.slug = (original[:head_len].rstrip("-") or "s") + suffix
            counter += 1

        if locked_code is not None:
            self.code = locked_code
        else:
            self._assign_store_code_for_create()

        from django.core.files.storage import default_storage

        if self.pk and self.logo and not getattr(self.logo, "_committed", True):
            old_logo = (
                Store.objects.filter(pk=self.pk).values_list("logo", flat=True).first()
            )
            if old_logo:
                try:
                    default_storage.delete(old_logo)
                except Exception:
                    pass

        super().save(*args, **kwargs)

    def _assign_store_code_for_create(self) -> None:
        """Normalize and uniquify Store.code; caller must set a non-empty code (no slug/public_id fallbacks)."""
        explicit = re.sub(r"[^A-Z0-9]", "", (self.code or "").strip().upper())[:10]
        if not explicit:
            raise ValueError(
                "Store.code is required. Set a non-empty alphanumeric code before saving a new store."
            )
        base = explicit
        candidate = base[:10]
        other = Store.objects.all()
        if self.pk:
            other = other.exclude(pk=self.pk)
        n = 2
        while other.filter(code=candidate).exists():
            suffix = str(n)
            head = max(1, 10 - len(suffix))
            root = base[:head].rstrip() or base[:1]
            candidate = (root + suffix)[:10]
            n += 1
        self.code = candidate

    def __str__(self) -> str:
        return self.name

    def get_media_keys(self) -> list[str]:
        key = getattr(self.logo, "name", "") if self.logo else ""
        return [key] if key else []


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
        help_text="Feature/module flags for this store (e.g. products, orders).",
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text="Default low-stock alert threshold for inventory.",
    )
    extra_field_schema = models.JSONField(
        blank=True,
        default=list,
        help_text="Extra field definitions for products only: [{id, entityType, name, fieldType, required, order, options}]",
    )
    email_notify_owner_on_order_received = models.BooleanField(
        default=False,
        help_text="Premium: send email to store when a new order is placed.",
    )
    email_customer_on_order_confirmed = models.BooleanField(
        default=False,
        help_text="Premium: email customer when the order is sent to courier (dispatch).",
    )
    public_api_enabled = models.BooleanField(
        default=False,
        help_text="Allow public storefront read endpoints without API key for this store.",
    )
    storefront_public = models.JSONField(
        blank=True,
        default=dict,
        help_text="Public storefront-only data: theme_settings, country, seo, policy_urls, social_links, etc.",
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


class StoreApiKey(models.Model):
    """
    Store-scoped API key material (hash only).

    Plaintext API keys are never stored. Keys are validated by hashing incoming
    key material and matching the digest against key_hash.
    """

    class KeyType(models.TextChoices):
        PUBLIC = "public", "Public"
        SECRET = "secret", "Secret"

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. sak_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="HMAC-SHA256 digest of API key material.",
    )
    key_prefix = models.CharField(max_length=16, blank=True, default="")
    key_last4 = models.CharField(max_length=4, blank=True, default="")
    label = models.CharField(max_length=80, blank=True, default="")
    key_type = models.CharField(
        max_length=16,
        choices=KeyType.choices,
        default=KeyType.PUBLIC,
        db_index=True,
    )
    scopes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["store", "is_active", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("storeapikey")
        super().save(*args, **kwargs)
