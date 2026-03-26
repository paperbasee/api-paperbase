"""Central catalog of default transactional email templates."""

from __future__ import annotations

from .constants import (
    EMAIL_VERIFICATION,
    GENERIC_NOTIFICATION,
    ORDER_CONFIRMED,
    ORDER_RECEIVED,
    PASSWORD_RESET,
    PLATFORM_NEW_SUBSCRIPTION,
    SUBSCRIPTION_ACTIVATED,
    SUBSCRIPTION_CHANGED,
    SUBSCRIPTION_PAYMENT,
    TWO_FA_CODE,
    TWO_FA_DISABLE,
    TWO_FA_RECOVERY,
)


DEFAULT_EMAIL_TEMPLATES: dict[str, dict[str, str]] = {
    EMAIL_VERIFICATION: {
        "subject": "Verify your email address",
        "html_body": (
            "<p>Hello {{ user_name|default:user_email }},</p>"
            "<p>Please verify your email by clicking the link below:</p>"
            '<p><a href="{{ verification_link }}">Verify Email</a></p>'
        ),
        "text_body": (
            "Hello {{ user_name|default:user_email }},\n"
            "Please verify your email: {{ verification_link }}\n"
        ),
    },
    PASSWORD_RESET: {
        "subject": "Reset your password",
        "html_body": (
            "<p>Hello {{ user_name|default:user_email }},</p>"
            "<p>Use the link below to reset your password:</p>"
            '<p><a href="{{ reset_link }}">Reset Password</a></p>'
        ),
        "text_body": (
            "Hello {{ user_name|default:user_email }},\n"
            "Reset your password: {{ reset_link }}\n"
        ),
    },
    ORDER_RECEIVED: {
        "subject": "New order {{ order_number }} received",
        "html_body": (
            "<p>Store: {{ store_name }}</p>"
            "<p>Order: {{ order_number }}</p>"
            "<p>Customer: {{ customer_name }} ({{ customer_email }})</p>"
            "<p>Total: {{ total }} {{ currency }}</p>"
        ),
        "text_body": (
            "Store: {{ store_name }}\n"
            "Order: {{ order_number }}\n"
            "Customer: {{ customer_name }} ({{ customer_email }})\n"
            "Total: {{ total }} {{ currency }}\n"
        ),
    },
    ORDER_CONFIRMED: {
        "subject": "Your order {{ order_number }} is confirmed",
        "html_body": (
            "<p>Hello {{ customer_name|default:'Customer' }},</p>"
            "<p>Your order {{ order_number }} at {{ store_name }} is confirmed.</p>"
            "<p>Total: {{ total }} {{ currency }}</p>"
        ),
        "text_body": (
            "Hello {{ customer_name|default:'Customer' }},\n"
            "Your order {{ order_number }} at {{ store_name }} is confirmed.\n"
            "Total: {{ total }} {{ currency }}\n"
        ),
    },
    SUBSCRIPTION_PAYMENT: {
        "subject": "Subscription payment receipt",
        "html_body": (
            "<p>Hello {{ user_name }},</p>"
            "<p>We received your payment for {{ plan_name }}.</p>"
            "<p>Amount: {{ amount }} {{ currency }}</p>"
        ),
        "text_body": (
            "Hello {{ user_name }},\n"
            "We received your payment for {{ plan_name }}.\n"
            "Amount: {{ amount }} {{ currency }}\n"
        ),
    },
    SUBSCRIPTION_ACTIVATED: {
        "subject": "Subscription activated: {{ plan_name }}",
        "html_body": (
            "<p>Hello {{ user_name }},</p>"
            "<p>Your subscription {{ plan_name }} is now active.</p>"
            "<p>Status: {{ subscription_status }}</p>"
        ),
        "text_body": (
            "Hello {{ user_name }},\n"
            "Your subscription {{ plan_name }} is now active.\n"
            "Status: {{ subscription_status }}\n"
        ),
    },
    SUBSCRIPTION_CHANGED: {
        "subject": "Subscription updated",
        "html_body": (
            "<p>Hello {{ user_name }},</p>"
            "<p>Your subscription changed from {{ old_plan_name }} to {{ new_plan_name }}.</p>"
            "<p>Effective date: {{ effective_date }}</p>"
        ),
        "text_body": (
            "Hello {{ user_name }},\n"
            "Your subscription changed from {{ old_plan_name }} to {{ new_plan_name }}.\n"
            "Effective date: {{ effective_date }}\n"
        ),
    },
    PLATFORM_NEW_SUBSCRIPTION: {
        "subject": "New store subscription activated",
        "html_body": (
            "<p>Store: {{ store_name }}</p>"
            "<p>Owner email: {{ store_owner_email }}</p>"
            "<p>Plan: {{ plan_name }}</p>"
            "<p>Status: {{ subscription_status }}</p>"
        ),
        "text_body": (
            "Store: {{ store_name }}\n"
            "Owner email: {{ store_owner_email }}\n"
            "Plan: {{ plan_name }}\n"
            "Status: {{ subscription_status }}\n"
        ),
    },
    TWO_FA_DISABLE: {
        "subject": "Two-factor authentication disabled",
        "html_body": (
            "<p>Hello {{ user_name|default:user_email }},</p>"
            "<p>Two-factor authentication was disabled for your account.</p>"
            "<p>If this was not you, contact support immediately.</p>"
        ),
        "text_body": (
            "Hello {{ user_name|default:user_email }},\n"
            "Two-factor authentication was disabled for your account.\n"
            "If this was not you, contact support immediately.\n"
        ),
    },
    TWO_FA_RECOVERY: {
        "subject": "Your 2FA recovery code",
        "html_body": (
            "<p>Hello {{ user_name|default:'User' }},</p>"
            "<p>Your recovery code is: <strong>{{ code }}</strong></p>"
            "<p>Expires at: {{ expires_at }}</p>"
        ),
        "text_body": (
            "Hello {{ user_name|default:'User' }},\n"
            "Your recovery code is: {{ code }}\n"
            "Expires at: {{ expires_at }}\n"
        ),
    },
    TWO_FA_CODE: {
        "subject": "Your verification code",
        "html_body": (
            "<p>Hello,</p>"
            "<p>Your verification code is: <strong>{{ code }}</strong></p>"
        ),
        "text_body": "Your verification code is: {{ code }}\n",
    },
    GENERIC_NOTIFICATION: {
        "subject": "{{ title|default:'Notification' }}",
        "html_body": (
            "<p>{{ title|default:'Notification' }}</p>"
            "<p>{{ body }}</p>"
            "{% if action_url %}<p><a href=\"{{ action_url }}\">Open</a></p>{% endif %}"
        ),
        "text_body": (
            "{{ title|default:'Notification' }}\n"
            "{{ body }}\n"
            "{% if action_url %}{{ action_url }}{% endif %}\n"
        ),
    },
}

