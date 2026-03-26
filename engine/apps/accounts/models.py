from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from engine.core.ids import generate_public_id
from engine.core.models import PublicIdMixin
from engine.core.encryption import decrypt_value, encrypt_value


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, username=None, **extra_fields):
        if not email:
            raise ValueError("Email address is required.")
        email = self.normalize_email(email)
        # Backwards-compat: some legacy code/tests may still pass `username`.
        # This project uses email as the login identifier, so we intentionally ignore it.
        extra_fields.pop("username", None)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, username=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_verified", True)
        extra_fields.pop("username", None)

        if not extra_fields.get("is_staff"):
            raise ValueError("Superuser must have is_staff=True.")
        if not extra_fields.get("is_superuser"):
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Production-grade custom user model using email as the unique identifier.

    public_id (usr_xxx) is used for all external API exposure.
    Internal integer PK is used for DB joins and FK references.
    """

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False
    )
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)

    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    session_version = models.IntegerField(default=0)

    date_joined = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["public_id"]),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("user")
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.first_name


class SuperUserManager(BaseUserManager):
    def get_queryset(self):
        return super().get_queryset().filter(is_superuser=True)


class SuperUser(User):
    """Proxy model — admin accounts only (is_superuser=True). No separate DB table."""

    objects = SuperUserManager()

    class Meta:
        proxy = True
        verbose_name = "superuser"
        verbose_name_plural = "superusers"


class StoreUserManager(BaseUserManager):
    def get_queryset(self):
        return super().get_queryset().filter(is_superuser=False)


class StoreUser(User):
    """Proxy model — store owner/staff accounts only (is_superuser=False). No separate DB table."""

    objects = StoreUserManager()

    class Meta:
        proxy = True
        verbose_name = "user"
        verbose_name_plural = "users"


class UserTwoFactor(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="two_factor_profile",
    )
    secret_encrypted = models.TextField(blank=True, default="")
    pending_secret_encrypted = models.TextField(blank=True, default="")
    is_enabled = models.BooleanField(default=False)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_enabled"]),
        ]

    @property
    def secret(self) -> str:
        return decrypt_value(self.secret_encrypted)

    @secret.setter
    def secret(self, value: str) -> None:
        self.secret_encrypted = encrypt_value(value or "")

    @property
    def pending_secret(self) -> str:
        return decrypt_value(self.pending_secret_encrypted)

    @pending_secret.setter
    def pending_secret(self, value: str) -> None:
        self.pending_secret_encrypted = encrypt_value(value or "")

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())


class UserTwoFactorRecoveryCode(PublicIdMixin, models.Model):
    """One-time email recovery code for disabling 2FA when the authenticator is unavailable."""

    PUBLIC_ID_KIND = "twofarecovery"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="two_factor_recovery_codes",
    )
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "used_at", "expires_at"]),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)


class UserTwoFactorChallenge(models.Model):
    class Flow(models.TextChoices):
        LOGIN = "login", "Login"
        REGISTER = "register", "Register"
        SWITCH_STORE = "switch_store", "Switch Store"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="two_factor_challenges",
    )
    flow = models.CharField(max_length=20, choices=Flow.choices)
    challenge_id = models.CharField(max_length=64, unique=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "flow"]),
            models.Index(fields=["expires_at"]),
        ]

    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())
