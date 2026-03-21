from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from engine.apps.notifications.models import SystemNotification

User = get_user_model()

ACTIVE_URL = "/api/v1/system-notifications/active/"


class ActiveSystemNotificationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
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
        n = SystemNotification.objects.create(
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
        SystemNotification.objects.create(
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
        SystemNotification.objects.create(
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
        SystemNotification.objects.create(
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
        low = SystemNotification.objects.create(
            title="Low",
            message="B",
            is_active=True,
            priority=0,
            start_at=now - timedelta(hours=1),
        )
        high = SystemNotification.objects.create(
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
