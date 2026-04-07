from unittest.mock import patch

from django.test import SimpleTestCase

from engine.apps.emails.providers.resend import ResendEmailProvider
from engine.apps.emails.router import resolve_email_sender


class EmailRouterTests(SimpleTestCase):
    def test_security_email_sender(self):
        self.assertEqual(
            resolve_email_sender("PASSWORD_RESET"), "Paperbase <security@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("EMAIL_VERIFICATION"), "Paperbase <security@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("TWO_FA_RECOVERY"), "Paperbase <security@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("TWO_FA_DISABLE"), "Paperbase <security@mail.paperbase.me>"
        )

    def test_billing_email_sender(self):
        self.assertEqual(
            resolve_email_sender("SUBSCRIPTION_PAYMENT"), "Paperbase <billing@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("SUBSCRIPTION_ACTIVATED"), "Paperbase <billing@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("SUBSCRIPTION_CHANGED"), "Paperbase <billing@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("PLATFORM_NEW_SUBSCRIPTION"), "Paperbase <billing@mail.paperbase.me>"
        )

    def test_transactional_email_sender(self):
        self.assertEqual(
            resolve_email_sender("ORDER_CONFIRMED"), "Paperbase <noreply@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("ORDER_RECEIVED"), "Paperbase <noreply@mail.paperbase.me>"
        )
        self.assertEqual(
            resolve_email_sender("GENERIC_NOTIFICATION"), "Paperbase <noreply@mail.paperbase.me>"
        )

    def test_fallback_sender(self):
        self.assertEqual(resolve_email_sender("UNKNOWN_TYPE"), "Paperbase <noreply@mail.paperbase.me>")
        self.assertEqual(resolve_email_sender(""), "Paperbase <noreply@mail.paperbase.me>")


class ResendProviderRoutingTests(SimpleTestCase):
    @patch("engine.apps.emails.providers.resend.requests.post")
    def test_resend_never_uses_onboarding_sender(self, mock_post):
        mock_response = mock_post.return_value
        mock_response.status_code = 200
        mock_response.content = b"{}"
        mock_response.json.return_value = {}

        provider = ResendEmailProvider(api_key="test-key")
        provider.send(
            "PASSWORD_RESET",
            "user@example.com",
            "Password reset",
            "<p>Reset</p>",
        )

        payload = mock_post.call_args.kwargs["data"]
        self.assertIn('"from": "Paperbase <security@mail.paperbase.me>"', payload)
        legacy_sender = "onboarding@" + "resend.dev"
        self.assertNotIn(legacy_sender, payload)
