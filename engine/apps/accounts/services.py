from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from engine.apps.emails.constants import EMAIL_VERIFICATION
from engine.apps.emails.tasks import send_email_task

User = get_user_model()

RESEND_VERIFICATION_NEUTRAL_MESSAGE = "If the email exists, verification link has been sent."


def _uid_for(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


def send_verification_email(user):
    uid = _uid_for(user)
    token = default_token_generator.make_token(user)
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    link = f"{frontend_url}/auth/verify-email?uid={uid}&token={token}"
    send_email_task.delay(
        EMAIL_VERIFICATION,
        user.email,
        {
            "user_name": user.get_short_name() or user.email,
            "user_email": user.email,
            "verification_link": link,
        },
    )


def resend_verification_email_for_email(email: str):
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return

    user = User.objects.filter(email__iexact=normalized_email).first()
    if user is None or user.is_verified:
        return

    send_verification_email(user)


def invalidate_other_device_sessions(user):
    """
    Blacklist all outstanding refresh tokens for this user.
    Access tokens naturally expire based on ACCESS_TOKEN_LIFETIME.
    """
    tokens = OutstandingToken.objects.filter(user=user)
    for token in tokens:
        BlacklistedToken.objects.get_or_create(token=token)


def reset_user_password(user, new_password: str, logout_all_devices: bool = False):
    user.set_password(new_password)
    update_fields = ["password", "updated_at"]
    if logout_all_devices:
        user.session_version = user.session_version + 1
        update_fields.append("session_version")
    user.save(update_fields=update_fields)
    if logout_all_devices:
        invalidate_other_device_sessions(user)


def change_user_password(user, new_password: str, logout_all_devices: bool = False):
    user.set_password(new_password)
    update_fields = ["password", "updated_at"]
    if logout_all_devices:
        user.session_version = user.session_version + 1
        update_fields.append("session_version")
    user.save(update_fields=update_fields)
    if logout_all_devices:
        invalidate_other_device_sessions(user)
