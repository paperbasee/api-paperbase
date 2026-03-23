from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from engine.apps.notifications.models import NotificationDismissal, PlatformNotification

User = get_user_model()

ACTIVE_URL = "/api/v1/system-notifications/active/"


class ActiveSystemNotificationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Platform host: default test client host "testserver" is not in PLATFORM_HOSTS.
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.staff = User.objects.create_user(
            email="banner-staff@example.com",
            password="secret123",
            is_staff=True,
        )

    def test_unauthenticated_is_denied(self):
        response = self.client.get(ACTIVE_URL)
        self.assertIn(response.status_code, (401, 403))

    def test_active_notification_shape_no_internal_id(self):
        now = timezone.now()
        n = PlatformNotification.objects.create(
            title="Ship notice",
            message="We updated shipping.",
            cta_text="Details",
            cta_url="https://example.com/changelog",
            is_active=True,
            start_at=now - timedelta(hours=1),
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(ACTIVE_URL)
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertIsInstance(data, dict)
        self.assertEqual(data["public_id"], n.public_id)
        self.assertEqual(data["title"], "Ship notice")
        self.assertEqual(data["message"], "We updated shipping.")
        self.assertEqual(data["cta_text"], "Details")
        self.assertEqual(data["cta_url"], "https://example.com/changelog")
        self.assertNotIn("id", data)

    def test_no_match_returns_null(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(ACTIVE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data)

    def test_future_start_excluded(self):
        now = timezone.now()
        PlatformNotification.objects.create(
            title="Future",
            message="Soon",
            is_active=True,
            start_at=now + timedelta(days=1),
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(ACTIVE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data)

    def test_past_end_excluded(self):
        now = timezone.now()
        PlatformNotification.objects.create(
            title="Expired",
            message="Old",
            is_active=True,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=1),
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(ACTIVE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data)

    def test_inactive_excluded(self):
        now = timezone.now()
        PlatformNotification.objects.create(
            title="Off",
            message="Hidden",
            is_active=False,
            start_at=now - timedelta(hours=1),
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(ACTIVE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data)

    def test_higher_priority_wins(self):
        now = timezone.now()
        low = PlatformNotification.objects.create(
            title="Low",
            message="B",
            is_active=True,
            priority=0,
            start_at=now - timedelta(hours=1),
        )
        high = PlatformNotification.objects.create(
            title="High",
            message="A",
            is_active=True,
            priority=10,
            start_at=now - timedelta(hours=1),
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(ACTIVE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["public_id"], high.public_id)
        self.assertNotEqual(response.data["public_id"], low.public_id)


class SystemNotificationDismissAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.staff = User.objects.create_user(
            email="dismiss-staff@example.com",
            password="secret123",
            is_staff=True,
        )
        self.other = User.objects.create_user(
            email="dismiss-other@example.com",
            password="secret123",
            is_staff=True,
        )

    def _active_banner(self):
        now = timezone.now()
        return PlatformNotification.objects.create(
            title="Banner",
            message="Hello",
            is_active=True,
            start_at=now - timedelta(hours=1),
            daily_limit=2,
        )

    def _dismiss_url(self, public_id):
        return f"/api/v1/system-notifications/{public_id}/dismiss/"

    def test_dismiss_post_returns_shape_no_id(self):
        n = self._active_banner()
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(self._dismiss_url(n.public_id))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("id", r.data)
        self.assertEqual(r.data["public_id"], n.public_id)
        self.assertEqual(r.data["dismiss_count"], 1)
        self.assertFalse(r.data["hidden"])

    def test_dismiss_unknown_public_id_404(self):
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(self._dismiss_url("sys_nonexistent00000000001"))
        self.assertEqual(r.status_code, 404)

    def test_dismiss_until_limit_then_get_null(self):
        n = self._active_banner()
        self.client.force_authenticate(user=self.staff)
        r1 = self.client.post(self._dismiss_url(n.public_id))
        self.assertEqual(r1.data["dismiss_count"], 1)
        self.assertFalse(r1.data["hidden"])
        g1 = self.client.get(ACTIVE_URL)
        self.assertEqual(g1.data["public_id"], n.public_id)
        r2 = self.client.post(self._dismiss_url(n.public_id))
        self.assertEqual(r2.data["dismiss_count"], 2)
        self.assertTrue(r2.data["hidden"])
        g2 = self.client.get(ACTIVE_URL)
        self.assertIsNone(g2.data)

    def test_each_dismiss_click_increments_count_independently(self):
        now = timezone.now()
        n = PlatformNotification.objects.create(
            title="Independent clicks",
            message="Every click should count",
            is_active=True,
            start_at=now - timedelta(hours=1),
            daily_limit=3,
        )
        self.client.force_authenticate(user=self.staff)

        r1 = self.client.post(self._dismiss_url(n.public_id))
        self.assertEqual(r1.data["dismiss_count"], 1)
        self.assertFalse(r1.data["hidden"])
        self.assertEqual(self.client.get(ACTIVE_URL).data["public_id"], n.public_id)

        r2 = self.client.post(self._dismiss_url(n.public_id))
        self.assertEqual(r2.data["dismiss_count"], 2)
        self.assertFalse(r2.data["hidden"])
        self.assertEqual(self.client.get(ACTIVE_URL).data["public_id"], n.public_id)

        r3 = self.client.post(self._dismiss_url(n.public_id))
        self.assertEqual(r3.data["dismiss_count"], 3)
        self.assertTrue(r3.data["hidden"])
        self.assertIsNone(self.client.get(ACTIVE_URL).data)

    def test_other_user_not_affected_by_dismiss_limit(self):
        n = self._active_banner()
        self.client.force_authenticate(user=self.staff)
        self.client.post(self._dismiss_url(n.public_id))
        self.client.post(self._dismiss_url(n.public_id))
        self.assertIsNone(self.client.get(ACTIVE_URL).data)
        self.client.force_authenticate(user=self.other)
        go = self.client.get(ACTIVE_URL)
        self.assertEqual(go.data["public_id"], n.public_id)

    def test_yesterday_exhaust_does_not_block_today(self):
        n = self._active_banner()
        today = timezone.localtime(timezone.now()).date()
        yesterday = today - timedelta(days=1)
        NotificationDismissal.objects.create(
            user=self.staff,
            notification=n,
            date=yesterday,
            dismiss_count=99,
        )
        self.client.force_authenticate(user=self.staff)
        g = self.client.get(ACTIVE_URL)
        self.assertEqual(g.data["public_id"], n.public_id)
