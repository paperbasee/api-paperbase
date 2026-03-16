"""
Meta Conversions API service.

Sends server-side events to the Meta Graph API for ad attribution and
audience optimization. All events are dispatched in a background thread
so they never block the main request/response cycle.

PII fields (email, phone, name, etc.) are SHA-256 hashed before
transmission as required by Meta's data policy.
"""
import hashlib
import logging
import threading
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_meta_settings():
    return {
        'pixel_id': getattr(settings, 'META_PIXEL_ID', ''),
        'access_token': getattr(settings, 'META_ACCESS_TOKEN', ''),
        'api_version': getattr(settings, 'META_API_VERSION', 'v25.0'),
        'test_event_code': getattr(settings, 'META_TEST_EVENT_CODE', ''),
    }


class MetaConversionsService:
    API_URL = 'https://graph.facebook.com/{version}/{pixel_id}/events'

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    def _hash(self, value: str | None) -> str | None:
        """SHA-256 hash a PII value (lowercase + stripped) as Meta requires."""
        if not value:
            return None
        return hashlib.sha256(value.strip().lower().encode()).hexdigest()

    def _get_client_ip(self, request) -> str:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    def _build_user_data(self, request, extra: dict | None = None) -> dict:
        """
        Build the user_data payload from the current request.

        extra can contain pre-validated fields such as:
          em, ph, fn, ln, ct, st, zp, country, external_id, subscription_id
        These are hashed automatically if they contain PII.
        """
        extra = extra or {}

        user_data: dict = {
            'client_ip_address': self._get_client_ip(request),
            'client_user_agent': request.META.get('HTTP_USER_AGENT', '') or None,
        }

        # fbc / fbp cookies forwarded by the frontend as custom headers
        fbc = request.META.get('HTTP_X_FBC') or request.META.get('HTTP_X_FBC_COOKIE')
        fbp = request.META.get('HTTP_X_FBP') or request.META.get('HTTP_X_FBP_COOKIE')
        if fbc:
            user_data['fbc'] = fbc
        if fbp:
            user_data['fbp'] = fbp

        # Hashed PII from authenticated user
        if request.user.is_authenticated:
            email = getattr(request.user, 'email', '') or ''
            if email and 'em' not in extra:
                user_data['em'] = [self._hash(email)]

        # Hashed PII from explicitly provided extra values
        for field in ('em', 'ph', 'fn', 'ln', 'ct', 'st', 'zp', 'country'):
            if field in extra and extra[field]:
                # em and ph may already be lists; wrap scalars
                hashed = self._hash(str(extra[field]))
                if hashed:
                    user_data[field] = [hashed]

        # Non-hashed fields
        for field in ('external_id', 'subscription_id'):
            if field in extra and extra[field]:
                user_data[field] = extra[field]

        # Remove None values
        return {k: v for k, v in user_data.items() if v is not None}

    def _get_event_source_url(self, request) -> str | None:
        return (
            request.META.get('HTTP_REFERER')
            or request.build_absolute_uri()
            or None
        )

    def _send(
        self,
        event_name: str,
        user_data: dict,
        custom_data: dict | None = None,
        event_id: str | None = None,
        source_url: str | None = None,
    ) -> None:
        """
        POST a single event to the Meta Conversions API.
        Called from a background thread — exceptions are caught and logged.
        """
        cfg = _get_meta_settings()
        if not cfg['pixel_id'] or not cfg['access_token']:
            logger.debug('META_PIXEL_ID or META_ACCESS_TOKEN not set; skipping event %s', event_name)
            return

        url = self.API_URL.format(version=cfg['api_version'], pixel_id=cfg['pixel_id'])
        params = {'access_token': cfg['access_token']}

        event: dict = {
            'event_name': event_name,
            'event_time': int(time.time()),
            'action_source': 'website',
            'user_data': user_data,
        }
        if source_url:
            event['event_source_url'] = source_url
        if event_id:
            event['event_id'] = event_id
        if custom_data:
            event['custom_data'] = custom_data

        payload: dict = {'data': [event]}
        if cfg['test_event_code']:
            payload['test_event_code'] = cfg['test_event_code']

        try:
            resp = requests.post(url, params=params, json=payload, timeout=5)
            if not resp.ok:
                logger.warning(
                    'Meta Conversions API error for event %s: %s %s',
                    event_name, resp.status_code, resp.text,
                )
            else:
                logger.debug('Meta Conversions API event sent: %s', event_name)
        except Exception:
            logger.exception('Failed to send Meta Conversions API event: %s', event_name)

    def _send_async(self, *args, **kwargs) -> None:
        """Fire-and-forget: dispatch _send in a daemon thread."""
        t = threading.Thread(target=self._send, args=args, kwargs=kwargs, daemon=True)
        t.start()

    # ---------------------------------------------------------------------------
    # Public event methods
    # ---------------------------------------------------------------------------

    def track_search(self, request, query: str) -> None:
        """Search event — fired when a product search query is executed."""
        user_data = self._build_user_data(request)
        custom_data = {'search_string': query} if query else {}
        self._send_async(
            'Search',
            user_data,
            custom_data=custom_data or None,
            source_url=self._get_event_source_url(request),
        )

    def track_view_content(self, request, product) -> None:
        """ViewContent event — fired when a product detail page is viewed."""
        user_data = self._build_user_data(request)
        custom_data = {
            'content_ids': [str(product.id)],
            'content_name': product.name,
            'content_type': 'product',
            'currency': 'BDT',
            'value': str(product.price),
        }
        self._send_async(
            'ViewContent',
            user_data,
            custom_data=custom_data,
            source_url=self._get_event_source_url(request),
        )

    def track_add_to_cart(self, request, product, quantity: int) -> None:
        """AddToCart event — fired when a product is added to the cart."""
        user_data = self._build_user_data(request)
        custom_data = {
            'content_ids': [str(product.id)],
            'content_name': product.name,
            'content_type': 'product',
            'currency': 'BDT',
            'value': str(product.price * quantity),
            'quantity': quantity,
        }
        self._send_async(
            'AddToCart',
            user_data,
            custom_data=custom_data,
            source_url=self._get_event_source_url(request),
        )

    def track_add_to_wishlist(self, request, product) -> None:
        """AddToWishlist event — fired when a product is added to the wishlist."""
        user_data = self._build_user_data(request)
        custom_data = {
            'content_ids': [str(product.id)],
            'content_name': product.name,
            'content_type': 'product',
            'currency': 'BDT',
            'value': str(product.price),
        }
        self._send_async(
            'AddToWishlist',
            user_data,
            custom_data=custom_data,
            source_url=self._get_event_source_url(request),
        )

    def track_contact(self, request) -> None:
        """Contact event — fired when a contact form is submitted."""
        user_data = self._build_user_data(request)
        self._send_async(
            'Contact',
            user_data,
            source_url=self._get_event_source_url(request),
        )

    def track_initiate_checkout(self, request) -> None:
        """InitiateCheckout event — fired when the user starts the checkout flow."""
        user_data = self._build_user_data(request)
        self._send_async(
            'InitiateCheckout',
            user_data,
            source_url=self._get_event_source_url(request),
        )

    def track_add_payment_info(self, request, order_data: dict | None = None) -> None:
        """
        AddPaymentInfo event — fired when shipping/payment details are submitted.
        order_data may contain: email, phone, shipping_name.
        """
        order_data = order_data or {}
        extra: dict = {}

        phone = order_data.get('phone', '')
        email = order_data.get('email', '')
        shipping_name = order_data.get('shipping_name', '')

        if phone:
            extra['ph'] = phone
        if email:
            extra['em'] = email
        if shipping_name:
            parts = shipping_name.strip().split(' ', 1)
            extra['fn'] = parts[0]
            if len(parts) > 1:
                extra['ln'] = parts[1]

        user_data = self._build_user_data(request, extra=extra)
        self._send_async(
            'AddPaymentInfo',
            user_data,
            source_url=self._get_event_source_url(request),
        )

    def track_purchase(self, request, order) -> None:
        """
        Purchase event — fired after an order is successfully created.
        Sends currency, value, and content_ids from the order items.
        """
        extra: dict = {}
        email = getattr(order, 'email', '') or ''
        phone = getattr(order, 'phone', '') or ''
        shipping_name = getattr(order, 'shipping_name', '') or ''

        if email:
            extra['em'] = email
        if phone:
            extra['ph'] = phone
        if shipping_name:
            parts = shipping_name.strip().split(' ', 1)
            extra['fn'] = parts[0]
            if len(parts) > 1:
                extra['ln'] = parts[1]

        user_data = self._build_user_data(request, extra=extra)

        content_ids = [str(item.product_id) for item in order.items.all()]
        custom_data = {
            'currency': 'BDT',
            'value': str(order.total),
            'content_ids': content_ids,
            'content_type': 'product',
            'order_id': order.order_number or str(order.id),
        }
        self._send_async(
            'Purchase',
            user_data,
            custom_data=custom_data,
            source_url=self._get_event_source_url(request),
        )


# Module-level singleton — import and use directly in views.
meta_conversions = MetaConversionsService()
