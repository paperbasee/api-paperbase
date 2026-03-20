import pyotp
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from engine.apps.accounts.models import UserTwoFactor
from engine.apps.stores.models import Store, StoreMembership

User = get_user_model()


class TwoFactorFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = Store.objects.create(
            name="2FA Store",
            domain="2fa.local",
            owner_name="Owner",
            owner_email="owner@2fa.local",
        )
        self.user = User.objects.create_user(email="owner@2fa.local", password="pass1234")
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )

    def _login(self):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": "pass1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")

    def test_enable_2fa_then_login_requires_challenge(self):
        self._login()

        setup_resp = self.client.get("/api/v1/auth/2fa/setup/")
        self.assertEqual(setup_resp.status_code, 200)
        self.assertIn("secret", setup_resp.data)
        otp = pyotp.TOTP(setup_resp.data["secret"]).now()

        verify_resp = self.client.post("/api/v1/auth/2fa/verify/", {"code": otp}, format="json")
        self.assertEqual(verify_resp.status_code, 200)
        self.assertTrue(verify_resp.data["is_enabled"])
        self.assertNotIn("id", verify_resp.data)

        self.client.credentials()
        login_resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": "pass1234"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 202)
        self.assertTrue(login_resp.data["2fa_required"])
        self.assertNotIn("access", login_resp.data)

    def test_challenge_verify_issues_tokens(self):
        profile, _ = UserTwoFactor.objects.get_or_create(user=self.user)
        secret = pyotp.random_base32()
        profile.secret = secret
        profile.is_enabled = True
        profile.save(update_fields=["secret_encrypted", "is_enabled", "updated_at"])

        login_resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": "pass1234"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 202)
        challenge_id = login_resp.data["challenge_id"]

        otp = pyotp.TOTP(secret).now()
        verify_resp = self.client.post(
            "/api/v1/auth/2fa/challenge/verify/",
            {"challenge_id": challenge_id, "code": otp},
            format="json",
        )
        self.assertEqual(verify_resp.status_code, 200)
        self.assertIn("access", verify_resp.data)
        self.assertIn("refresh", verify_resp.data)

    def test_disable_requires_password_and_otp(self):
        self._login()
        profile, _ = UserTwoFactor.objects.get_or_create(user=self.user)
        secret = pyotp.random_base32()
        profile.secret = secret
        profile.is_enabled = True
        profile.save(update_fields=["secret_encrypted", "is_enabled", "updated_at"])

        bad_resp = self.client.post(
            "/api/v1/auth/2fa/disable/",
            {"password": "wrong", "code": "123456"},
            format="json",
        )
        self.assertEqual(bad_resp.status_code, 400)

        good_resp = self.client.post(
            "/api/v1/auth/2fa/disable/",
            {"password": "pass1234", "code": pyotp.TOTP(secret).now()},
            format="json",
        )
        self.assertEqual(good_resp.status_code, 200)
        profile.refresh_from_db()
        self.assertFalse(profile.is_enabled)

    def test_challenge_verify_rejects_non_totp_code(self):
        profile, _ = UserTwoFactor.objects.get_or_create(user=self.user)
        secret = pyotp.random_base32()
        profile.secret = secret
        profile.is_enabled = True
        profile.save(update_fields=["secret_encrypted", "is_enabled", "updated_at"])

        login_resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": "pass1234"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 202)
        challenge_id = login_resp.data["challenge_id"]

        bad_verify = self.client.post(
            "/api/v1/auth/2fa/challenge/verify/",
            {"challenge_id": challenge_id, "code": "ABCD-1234"},
            format="json",
        )
        self.assertEqual(bad_verify.status_code, 400)
