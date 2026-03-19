from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from engine.core.ids import generate_public_id


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_verified", True)

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
