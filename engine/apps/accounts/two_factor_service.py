import base64
import io
import secrets
from datetime import timedelta

import pyotp
import qrcode
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from engine.apps.accounts.models import (
    UserTwoFactor,
    UserTwoFactorChallenge,
)


TWO_FACTOR_OTP_DIGITS = 6
TWO_FACTOR_MAX_ATTEMPTS = 5
TWO_FACTOR_LOCK_MINUTES = 10
TWO_FACTOR_CHALLENGE_TTL_MINUTES = 5


def get_or_create_profile(user):
    profile, _ = UserTwoFactor.objects.get_or_create(user=user)
    return profile


def build_provisioning(user, secret: str):
    issuer = getattr(settings, "TWO_FACTOR_ISSUER_NAME", "Gadzilla")
    totp = pyotp.TOTP(secret, digits=TWO_FACTOR_OTP_DIGITS)
    uri = totp.provisioning_uri(name=user.email, issuer_name=issuer)

    image = qrcode.make(uri)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    encoded = base64.b64encode(stream.getvalue()).decode()
    qr_data_url = f"data:image/png;base64,{encoded}"
    return uri, qr_data_url


def begin_setup(user):
    profile = get_or_create_profile(user)
    secret = pyotp.random_base32()
    profile.pending_secret = secret
    profile.save(update_fields=["pending_secret_encrypted", "updated_at"])
    uri, qr_data_url = build_provisioning(user, secret)
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


def disable_2fa(user, otp_code: str):
    profile = get_or_create_profile(user)
    if not profile.is_enabled:
        return True, None
    ok, err = _verify_totp(profile, otp_code, use_pending=False)
    if not ok:
        return False, err
    profile.secret_encrypted = ""
    profile.pending_secret_encrypted = ""
    profile.is_enabled = False
    profile.last_used_step = None
    profile.save(update_fields=["secret_encrypted", "pending_secret_encrypted", "is_enabled", "last_used_step", "updated_at"])
    return True, None


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
def verify_challenge(challenge_id: str, otp_code: str):
    try:
        challenge = (
            UserTwoFactorChallenge.objects.select_for_update()
            .select_related("user")
            .get(challenge_id=challenge_id)
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
