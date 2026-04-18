"""Tracking ingest: JSON-only contract and no form-field explosion."""

from urllib.parse import urlencode

from django.test import Client, TestCase


class TrackingEventIngestContentTypeTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_urlencoded_many_fields_returns_415_not_500(self):
        """
        Huge application/x-www-form-urlencoded bodies used to trip
        DATA_UPLOAD_MAX_NUMBER_FIELDS (TooManyFieldsSent). Reject non-JSON
        before Django parses the form.
        """
        fields = {f"f{i}": "x" for i in range(2500)}
        body = urlencode(fields).encode("utf-8")
        response = self.client.post(
            "/tracking/event",
            data=body,
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 415)
        self.assertIn(b"application/json", response.content.lower())

    def test_json_without_auth_returns_401(self):
        response = self.client.post(
            "/tracking/event",
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
