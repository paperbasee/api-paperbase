from django.conf import settings
from django.db import models

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


class Customer(models.Model):
    """Profile and preferences for a store customer (per-store profile for a user)."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. cus_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="customers",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_profiles",
    )
    phone = models.CharField(max_length=20, blank=True)
    marketing_opt_in = models.BooleanField(default=False)
    default_shipping_address = models.ForeignKey(
        'customers.CustomerAddress',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    default_billing_address = models.ForeignKey(
        'customers.CustomerAddress',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    extra_data = models.JSONField(
        blank=True,
        default=dict,
        help_text="Dynamic extra fields per extra_field_schema.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "user"],
                name="uniq_customer_store_user",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("customer")
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.user)


class CustomerAddress(models.Model):
    """Saved address for a customer (shipping/billing)."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. adr_xxx).",
    )

    class Label(models.TextChoices):
        HOME = 'home', 'Home'
        OFFICE = 'office', 'Office'
        OTHER = 'other', 'Other'

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='addresses',
    )
    label = models.CharField(max_length=20, choices=Label.choices, default=Label.HOME)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100)
    is_default_shipping = models.BooleanField(default=False)
    is_default_billing = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['customer', 'label']

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("address")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer} - {self.label}"
