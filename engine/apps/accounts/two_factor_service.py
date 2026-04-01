import base64
import io
import secrets
from datetime import timedelta

import pyotp
import qrcode
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from engine.apps.accounts.models import (
    UserTwoFactor,
    UserTwoFactorChallenge,
    UserTwoFactorRecoveryCode,
)
from engine.apps.emails.constants import TWO_FA_RECOVERY
from engine.apps.emails.display_time import format_email_datetime
from engine.apps.emails.tasks import send_email_task
from engine.core.rate_limit_service import enforce_rate_limit, record_action
from engine.core.tenancy import get_active_store


TWO_FACTOR_OTP_DIGITS = 6
TWO_FACTOR_MAX_ATTEMPTS = 5
TWO_FACTOR_LOCK_MINUTES = 10
TWO_FACTOR_CHALLENGE_TTL_MINUTES = 5
RECOVERY_CODE_TTL_MINUTES = 20


def get_or_create_profile(user):
    profile, _ = UserTwoFactor.objects.get_or_create(user=user)
    return profile


def resolve_two_factor_issuer(request) -> str:
    """
    Use the authenticated user's active store name when available (dashboard JWT/header).
    Optional settings.TWO_FACTOR_ISSUER_NAME overrides when no store context applies.
    """
    ctx = get_active_store(request)
    if ctx.store and ctx.membership:
        name = (ctx.store.name or "").strip()
        if name:
            return name
    configured = getattr(settings, "TWO_FACTOR_ISSUER_NAME", None)
    if configured:
        s = str(configured).strip()
        if s:
            return s
    return "Paperbase"


def build_provisioning(user, secret: str, issuer_name: str):
    totp = pyotp.TOTP(secret, digits=TWO_FACTOR_OTP_DIGITS)
    uri = totp.provisioning_uri(name=user.email, issuer_name=issuer_name)

    image = qrcode.make(uri)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    encoded = base64.b64encode(stream.getvalue()).decode()
    qr_data_url = f"data:image/png;base64,{encoded}"
    return uri, qr_data_url


def begin_setup(user, *, issuer_name: str):
    profile = get_or_create_profile(user)
    secret = pyotp.random_base32()
    profile.pending_secret = secret
    profile.save(update_fields=["pending_secret_encrypted", "updated_at"])
    uri, qr_data_url = build_provisioning(user, secret, issuer_name)
    return {"secret": secret, "provisioning_uri": uri, "qr_code": qr_data_url}


def _verify_totp(profile: UserTwoFactor, code: str, use_pending: bool = False):
    if profile.is_locked():
        return False, "Too many invalid attempts. Try again later."

    secret = profile.pending_secret if use_pending else profile.secret
    if not secret:
        return False, "Two-factor setup is missing."

    totp = pyotp.TOTP(secret, digits=TWO_FACTOR_OTP_DIGITS)
    now = timezone.now()
    current_step = totp.timecode(now)
    if profile.last_used_step == current_step:
        return False, "Code was already used. Try a fresh code."

    valid = totp.verify((code or "").strip(), valid_window=1)
    if valid:
        profile.failed_attempts = 0
        profile.locked_until = None
        profile.last_used_step = current_step
        profile.save(update_fields=["failed_attempts", "locked_until", "last_used_step", "updated_at"])
        return True, None

    profile.failed_attempts += 1
    if profile.failed_attempts >= TWO_FACTOR_MAX_ATTEMPTS:
        profile.locked_until = now + timedelta(minutes=TWO_FACTOR_LOCK_MINUTES)
        profile.failed_attempts = 0
    profile.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    return False, "Invalid verification code."


def verify_setup_code(user, code: str):
    profile = get_or_create_profile(user)
    ok, err = _verify_totp(profile, code, use_pending=True)
    if not ok:
        return False, err
    profile.secret_encrypted = profile.pending_secret_encrypted
    profile.pending_secret_encrypted = ""
    profile.is_enabled = True
    profile.save(update_fields=["secret_encrypted", "pending_secret_encrypted", "is_enabled", "updated_at"])
    return True, None


def _clear_2fa_secrets(profile: UserTwoFactor) -> None:
    profile.secret_encrypted = ""
    profile.pending_secret_encrypted = ""
    profile.is_enabled = False
    profile.last_used_step = None
    profile.failed_attempts = 0
    profile.locked_until = None
    profile.save(
        update_fields=[
            "secret_encrypted",
            "pending_secret_encrypted",
            "is_enabled",
            "last_used_step",
            "failed_attempts",
            "locked_until",
            "updated_at",
        ]
    )


def disable_2fa(user, otp_code: str):
    profile = get_or_create_profile(user)
    if not profile.is_enabled:
        return True, None
    ok, err = _verify_totp(profile, otp_code, use_pending=False)
    if not ok:
        return False, err
    _clear_2fa_secrets(profile)
    return True, None


def request_recovery_code(user):
    """
    Issue a single-use recovery code (emailed in plaintext once). Invalidates prior unused codes.

    Raises ``RateLimitExceeded`` if the cooldown from a previous request is still active.
    """
    enforce_rate_limit(None, "2fa_recovery_request", user.email)

    profile = get_or_create_profile(user)
    if not profile.is_enabled:
        return False, "Two-factor authentication is not enabled."

    plain = secrets.token_hex(4).upper()
    code_hash = make_password(plain)
    expires_at = timezone.now() + timedelta(minutes=RECOVERY_CODE_TTL_MINUTES)

    with transaction.atomic():
        UserTwoFactorRecoveryCode.objects.filter(user=user, used_at__isnull=True).delete()
        UserTwoFactorRecoveryCode.objects.create(
            user=user,
            code_hash=code_hash,
            expires_at=expires_at,
        )

    send_email_task.delay(
        TWO_FA_RECOVERY,
        user.email,
        {
            "user_name": user.get_short_name() or user.email,
            "code": plain,
            "expires_at": format_email_datetime(expires_at),
        },
    )
    record_action(None, "2fa_recovery_request", user.email)
    return True, None


@transaction.atomic
def verify_recovery_and_disable_2fa(user, code: str):
    """Validate recovery code, mark used, clear 2FA secrets."""
    profile = get_or_create_profile(user)
    if not profile.is_enabled:
        return False, "Two-factor authentication is not enabled."

    normalized = "".join((code or "").split()).upper()
    if len(normalized) != 8 or not all(c in "0123456789ABCDEF" for c in normalized):
        return False, "Invalid recovery code."

    now = timezone.now()
    rows = (
        UserTwoFactorRecoveryCode.objects.select_for_update()
        .filter(user=user, used_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at")
    )

    for row in rows:
        if check_password(normalized, row.code_hash):
            row.used_at = now
            row.save(update_fields=["used_at", "updated_at"])
            _clear_2fa_secrets(profile)
            return True, None

    return False, "Invalid or expired recovery code."


def verify_login_otp(user, code: str):
    profile = get_or_create_profile(user)
    return _verify_totp(profile, code, use_pending=False)


@transaction.atomic
def create_challenge(user, flow: str, payload: dict | None = None):
    challenge = UserTwoFactorChallenge.objects.create(
        user=user,
        flow=flow,
        challenge_id=secrets.token_urlsafe(32),
        payload=payload or {},
        expires_at=timezone.now() + timedelta(minutes=TWO_FACTOR_CHALLENGE_TTL_MINUTES),
    )
    return challenge


@transaction.atomic
def verify_challenge(challenge_public_id: str, otp_code: str):
    try:
        challenge = (
            UserTwoFactorChallenge.objects.select_for_update()
            .select_related("user")
            .get(challenge_id=challenge_public_id)
        )
    except UserTwoFactorChallenge.DoesNotExist:
        return None, "Invalid challenge."

    if challenge.consumed_at:
        return None, "Challenge already used."
    if challenge.is_expired():
        return None, "Challenge expired."
    if challenge.is_locked():
        return None, "Too many invalid attempts. Try again later."

    ok, err = verify_login_otp(challenge.user, otp_code)
    if ok:
        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at", "updated_at"])
        return challenge, None

    challenge.failed_attempts += 1
    if challenge.failed_attempts >= TWO_FACTOR_MAX_ATTEMPTS:
        challenge.failed_attempts = 0
        challenge.locked_until = timezone.now() + timedelta(minutes=TWO_FACTOR_LOCK_MINUTES)
    challenge.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    return None, err
