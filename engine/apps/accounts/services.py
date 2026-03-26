from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

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
