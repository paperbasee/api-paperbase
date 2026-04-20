"""Central catalog of default transactional email templates."""

from __future__ import annotations

from .constants import (
    EMAIL_VERIFICATION,
    GENERIC_NOTIFICATION,
    ORDER_CONFIRMED,
    ORDER_RECEIVED,
    PASSWORD_RESET,
    PAYMENT_SUBMITTED,
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
        "subject": "Confirm your email address",
        "html_body": (
            "<p>Hello {{ user_name|default:user_email }},</p>"
            "<p>Thanks for signing up. Please confirm that this email address belongs to you "
            "so we can finish setting up your account and reach you about important activity.</p>"
            "<p><strong>Confirm your email</strong><br />"
            '<a href="{{ verification_link }}">Confirm</a></p>'
            "<p>If the link does not work, copy and paste this link into your browser:</p>"
            '<p style="word-break:break-all;">'
            '<a href="{{ verification_link }}">{{ verification_link }}</a></p>'
            "<p>If you did not create an account, you can ignore this message—no changes will be made.</p>"
        ),
        "text_body": (
            "Hello {{ user_name|default:user_email }},\n\n"
            "Thanks for signing up. Please confirm this email address so we can finish "
            "setting up your account.\n\n"
            "Confirm your email by opening this link in your browser:\n"
            "{{ verification_link }}\n\n"
            "If the link does not work, copy and paste it from the line above.\n\n"
            "If you did not create an account, you can ignore this email.\n"
        ),
    },
    PASSWORD_RESET: {
        "subject": "Password reset instructions",
        "html_body": (
            "<p>Hello {{ user_name|default:user_email }},</p>"
            "<p>We received a request to reset the password for the account linked to this email.</p>"
            "<p><strong>Reset your password</strong><br />"
            '<a href="{{ reset_link }}">{{ reset_link }}</a></p>'
            "<p>This link is intended for one-time use. After you choose a new password, "
            "any older reset links will stop working.</p>"
            "<p>If you did not ask for a reset, you can safely ignore this email—your password will stay the same. "
            "If you are worried someone else tried to access your account, change your password after signing in "
            "and consider enabling two-factor authentication.</p>"
        ),
        "text_body": (
            "Hello {{ user_name|default:user_email }},\n\n"
            "We received a request to reset the password for the account linked to this email.\n\n"
            "Reset your password (one-time link):\n"
            "{{ reset_link }}\n\n"
            "If you did not ask for a reset, ignore this email—your password will not change.\n"
        ),
    },
    ORDER_RECEIVED: {
        "subject": "New order {{ order_number }} — action required",
        "html_body": (
            "<p>You have a new order that needs your attention.</p>"
            "<p><strong>Order</strong> {{ order_number }}<br />"
            "<strong>Store</strong> {{ store_name }}<br />"
            "<strong>Order total</strong> {{ total }} {{ currency }}</p>"
            "<p><strong>Customer</strong><br />"
            "{{ customer_name|default:'—' }}<br />"
            "{{ customer_email }}</p>"
            "<p><strong>Delivery contact</strong><br />"
            "Phone: {{ phone|default:'—' }}<br />"
            "District: {{ district|default:'—' }}<br />"
            "Address: {{ shipping_address|default:'—' }}</p>"
            "{% if has_prepayment %}"
            "<p><strong>Prepayment</strong><br />"
            "Type: {{ prepayment_type }}<br />"
            "Status: {{ payment_status }}"
            "{% if transaction_id %}<br />Transaction ID: {{ transaction_id }}{% endif %}"
            "{% if payer_number %}<br />Payer number: {{ payer_number }}{% endif %}"
            "</p>"
            "{% endif %}"
            "<p><strong>Store contact for this order</strong><br />"
            "{{ store_contact_email|default:'—' }}</p>"
            "<p>Next steps: review the line items below, prepare or pack the order, and update fulfillment "
            "or courier status in your dashboard when ready. Reply to the customer’s email only if your "
            "workflow allows it.</p>"
            "<hr style=\"border:none;border-top:1px solid #e5e5e5;margin:16px 0;\" />"
            "<p><strong>Full order summary</strong></p>"
            "<div style=\"white-space:pre-line;font-family:ui-monospace,monospace;font-size:13px;\">"
            "{{ order_summary }}"
            "</div>"
        ),
        "text_body": (
            "NEW ORDER — please fulfill\n\n"
            "Order: {{ order_number }}\n"
            "Store: {{ store_name }}\n"
            "Total: {{ total }} {{ currency }}\n\n"
            "Customer: {{ customer_name|default:'—' }}\n"
            "Email: {{ customer_email }}\n"
            "Phone: {{ phone|default:'—' }}\n"
            "District: {{ district|default:'—' }}\n"
            "Address: {{ shipping_address|default:'—' }}\n\n"
            "{% if has_prepayment %}"
            "Prepayment: {{ prepayment_type }}\n"
            "Payment status: {{ payment_status }}\n"
            "{% if transaction_id %}Transaction ID: {{ transaction_id }}\n{% endif %}"
            "{% if payer_number %}Payer number: {{ payer_number }}\n{% endif %}"
            "\n"
            "{% endif %}"
            "Store contact: {{ store_contact_email|default:'—' }}\n\n"
            "Review the summary below, then update fulfillment in your dashboard.\n\n"
            "{{ order_summary }}\n"
        ),
    },
    ORDER_CONFIRMED: {
        "subject": "Your order #{{ order_number }} has been dispatched",
        "html_body": (
            "<p>Hello {{ customer_name|default:'there' }},</p>"
            "<p>Good news: <strong>{{ store_name }}</strong> has handed your order "
            "<strong>#{{ order_number }}</strong> to the courier. It is on its way to the address we have on file.</p>"
            "<p><strong>Order total</strong> {{ total }} {{ currency }}</p>"
            "{% if courier_provider_label or courier_consignment_id %}"
            "<p><strong>Shipping details</strong><br />"
            "{% if courier_provider_label %}Carrier: {{ courier_provider_label }}<br />{% endif %}"
            "{% if courier_consignment_id %}"
            "Tracking or consignment reference: {{ courier_consignment_id }}<br />"
            "{% endif %}"
            "Use your carrier’s website or app with the reference above if tracking is available.</p>"
            "{% endif %}"
            "<p><strong>Delivery address on this order</strong><br />"
            "{{ shipping_address|default:'—' }}<br />"
            "District: {{ district|default:'—' }}<br />"
            "Contact phone: {{ phone|default:'—' }}</p>"
            "<p>If anything looks wrong (wrong address or missing items), contact the store as soon as possible "
            "using the details from your order confirmation or their website.</p>"
            "<p>Thank you for your purchase.</p>"
            "<hr style=\"border:none;border-top:1px solid #e5e5e5;margin:16px 0;\" />"
            "<p><strong>Order summary</strong></p>"
            "<div style=\"white-space:pre-line;font-family:ui-monospace,monospace;font-size:13px;\">"
            "{{ order_summary }}"
            "</div>"
        ),
        "text_body": (
            "Hello {{ customer_name|default:'there' }},\n\n"
            "Your order {{ order_number }} from {{ store_name }} has been handed to the courier and is on its way.\n\n"
            "Order total: {{ total }} {{ currency }}\n"
            "{% if courier_provider_label %}Carrier: {{ courier_provider_label }}\n{% endif %}"
            "{% if courier_consignment_id %}Tracking / consignment ID: {{ courier_consignment_id }}\n{% endif %}"
            "\n"
            "Delivery address: {{ shipping_address|default:'—' }}\n"
            "District: {{ district|default:'—' }}\n"
            "Phone: {{ phone|default:'—' }}\n\n"
            "If the address or items look wrong, contact the store promptly.\n\n"
            "Thank you for your purchase.\n\n"
            "---\n"
            "{{ order_summary }}\n"
        ),
    },
    PAYMENT_SUBMITTED: {
        "subject": "Payment submitted for order {{ order_number }}",
        "html_body": (
            "<p>A customer has submitted payment details for an order. Review and verify it in your dashboard.</p>"
            "<p><strong>Order</strong> {{ order_number }}<br />"
            "<strong>Store</strong> {{ store_name }}<br />"
            "<strong>Order total</strong> {{ total }} {{ currency }}</p>"
            "<p><strong>Customer</strong><br />"
            "{{ customer_name|default:'—' }}<br />"
            "{{ customer_email|default:'—' }}</p>"
            "<p><strong>Payment details</strong><br />"
            "Prepayment type: {{ prepayment_type }}<br />"
            "Status: {{ payment_status }}<br />"
            "Transaction ID: {{ transaction_id|default:'—' }}<br />"
            "Payer number: {{ payer_number|default:'—' }}</p>"
            "<p><strong>Store contact for this order</strong><br />"
            "{{ store_contact_email|default:'—' }}</p>"
            "<p>Open the order in your dashboard to verify the transaction and move it to confirmed, "
            "or reject it if the details do not match.</p>"
        ),
        "text_body": (
            "PAYMENT SUBMITTED — please verify\n\n"
            "Order: {{ order_number }}\n"
            "Store: {{ store_name }}\n"
            "Total: {{ total }} {{ currency }}\n\n"
            "Customer: {{ customer_name|default:'—' }}\n"
            "Email: {{ customer_email|default:'—' }}\n\n"
            "Prepayment type: {{ prepayment_type }}\n"
            "Payment status: {{ payment_status }}\n"
            "Transaction ID: {{ transaction_id|default:'—' }}\n"
            "Payer number: {{ payer_number|default:'—' }}\n\n"
            "Store contact: {{ store_contact_email|default:'—' }}\n\n"
            "Verify the transaction in your dashboard to confirm or reject the order.\n"
        ),
    },
    SUBSCRIPTION_PAYMENT: {
        "subject": "Receipt: {{ plan_name }} payment received",
        "html_body": (
            "<p>Hello {{ user_name }},</p>"
            "<p>This email confirms we successfully received your payment for your subscription.</p>"
            "<p><strong>Plan</strong> {{ plan_name }}<br />"
            "<strong>Amount paid</strong> {{ amount }} {{ currency }}<br />"
            "<strong>Payment date</strong> {{ payment_date }}<br />"
            "<strong>Current period ends</strong> {{ billing_date }}</p>"
            "<p>Keep this message for your records. The same details may appear on your card or bank statement "
            "under the name of our payment processor.</p>"
            "<p>If you did not authorize this charge, contact support immediately using the contact information "
            "on our website or in your account.</p>"
        ),
        "text_body": (
            "Hello {{ user_name }},\n\n"
            "Payment receipt — subscription\n\n"
            "Plan: {{ plan_name }}\n"
            "Amount: {{ amount }} {{ currency }}\n"
            "Payment date: {{ payment_date }}\n"
            "Current subscription period ends: {{ billing_date }}\n\n"
            "Retain this email for your records.\n"
            "If you did not authorize this charge, contact support right away.\n"
        ),
    },
    SUBSCRIPTION_ACTIVATED: {
        "subject": "Your {{ plan_name }} subscription is active",
        "html_body": (
            "<p>Hello {{ user_name }},</p>"
            "<p>Your subscription is now <strong>active</strong>. You have access to everything included in "
            "<strong>{{ plan_name }}</strong> for the current billing period.</p>"
            "<p><strong>Status</strong> {{ subscription_status }}<br />"
            "<strong>Billing cycle</strong> {{ billing_cycle }}<br />"
            "<strong>Current period</strong> {{ start_date }} → {{ end_date }}</p>"
            "{% if payment_receipt_sent_separately %}"
            "<p>You should also receive a separate email with your payment receipt for this transaction.</p>"
            "{% else %}"
            "<p>Recent payment (if any): {{ amount }} {{ currency }} on {{ payment_date }}.</p>"
            "{% endif %}"
            "<p>To change your plan, update payment details, or cancel renewal, use the billing section of your account. "
            "You will receive email confirmations for important billing events.</p>"
        ),
        "text_body": (
            "Hello {{ user_name }},\n\n"
            "Your subscription is active.\n\n"
            "Plan: {{ plan_name }}\n"
            "Status: {{ subscription_status }}\n"
            "Billing cycle: {{ billing_cycle }}\n"
            "Current period: {{ start_date }} through {{ end_date }}\n\n"
            "{% if payment_receipt_sent_separately %}"
            "A separate email contains your payment receipt.\n"
            "{% else %}"
            "Payment on record: {{ amount }} {{ currency }} on {{ payment_date }}.\n"
            "{% endif %}\n"
            "Manage your plan and payment method from your account billing settings.\n"
        ),
    },
    SUBSCRIPTION_CHANGED: {
        "subject": "Your subscription plan was updated",
        "html_body": (
            "<p>Hello {{ user_name }},</p>"
            "<p>Your subscription has been updated as requested or as required by your last billing action.</p>"
            "<p><strong>Previous plan</strong> {{ old_plan_name }}<br />"
            "<strong>New plan</strong> {{ new_plan_name }}<br />"
            "<strong>Effective from</strong> {{ effective_date }}<br />"
            "<strong>Reason noted</strong> {{ change_reason }}</p>"
            "<p><strong>Current status</strong> {{ subscription_status }}<br />"
            "<strong>Current period ends</strong> {{ end_date }}</p>"
            "<p>Charges and feature access follow the new plan rules from the effective date onward. "
            "If this change was unexpected, review your account activity or contact support.</p>"
        ),
        "text_body": (
            "Hello {{ user_name }},\n\n"
            "Your subscription was updated.\n\n"
            "Previous plan: {{ old_plan_name }}\n"
            "New plan: {{ new_plan_name }}\n"
            "Effective date: {{ effective_date }}\n"
            "Reason: {{ change_reason }}\n\n"
            "Status: {{ subscription_status }}\n"
            "Current period ends: {{ end_date }}\n\n"
            "If you did not expect this change, check your account or contact support.\n"
        ),
    },
    PLATFORM_NEW_SUBSCRIPTION: {
        "subject": "Platform alert: new user subscription — {{ plan_name }}",
        "html_body": (
            "<p>A user has subscribed on the platform.</p>"
            "<p><strong>Plan</strong> {{ plan_name }}<br />"
            "<strong>Subscription status</strong> {{ subscription_status }}<br />"
            "<strong>Source</strong> {{ subscription_source }}</p>"
            "<p><strong>Account owner (auth)</strong><br />"
            "{% if user_full_name != store_owner_email %}{{ user_full_name }}<br />{% endif %}"
            "{{ store_owner_email }}<br />"
            "{% if user_public_id %}User ID: {{ user_public_id }}{% endif %}</p>"
            "<p><strong>Event time</strong> {{ timestamp }}</p>"
            "<p>Use this summary for support, fraud review, or revenue operations as needed.</p>"
        ),
        "text_body": (
            "New user subscription (platform)\n\n"
            "Plan: {{ plan_name }}\n"
            "Status: {{ subscription_status }}\n"
            "Source: {{ subscription_source }}\n\n"
            "Account owner (auth):\n"
            "{% if user_full_name == store_owner_email %}"
            "{{ store_owner_email }}\n"
            "{% else %}"
            "{{ user_full_name }}\n{{ store_owner_email }}\n"
            "{% endif %}"
            "{% if user_public_id %}User ID: {{ user_public_id }}\n{% endif %}\n"
            "Time: {{ timestamp }}\n"
        ),
    },
    TWO_FA_DISABLE: {
        "subject": "Security notice: two-factor authentication turned off",
        "html_body": (
            "<p>Hello {{ user_name|default:user_email }},</p>"
            "<p>Two-factor authentication (2FA) was <strong>disabled</strong> for your account.</p>"
            "<p><strong>When</strong> {{ disabled_at }}</p>"
            "<p>Only someone with access to your password could have done this. If it was you, no action is needed. "
            "If you did not turn off 2FA, sign in as soon as you can, turn 2FA back on, change your password, "
            "and contact support.</p>"
        ),
        "text_body": (
            "Hello {{ user_name|default:user_email }},\n\n"
            "Two-factor authentication was disabled for your account.\n"
            "Time: {{ disabled_at }}\n\n"
            "If this was not you, secure your account immediately: sign in, re-enable 2FA, change your password, "
            "and contact support.\n"
        ),
    },
    TWO_FA_RECOVERY: {
        "subject": "Your account recovery code",
        "html_body": (
            "<p>Hello {{ user_name|default:'there' }},</p>"
            "<p>You asked for a one-time recovery code to regain access to your account when your authenticator "
            "app is unavailable.</p>"
            "<p><strong>Your code</strong><br /><strong>{{ code }}</strong></p>"
            "<p><strong>Expires</strong> {{ expires_at }}</p>"
            "<ul>"
            "<li>Enter this code only on our official sign-in or recovery screen.</li>"
            "<li>Each new request invalidates any previous unused code.</li>"
            "<li>Do not forward this email or share the code with anyone.</li>"
            "</ul>"
            "<p>If you did not request this code, secure your account: change your password and contact support.</p>"
        ),
        "text_body": (
            "Hello {{ user_name|default:'there' }},\n\n"
            "Your one-time recovery code: {{ code }}\n"
            "Expires: {{ expires_at }}\n\n"
            "Use it only on our official site. A new request replaces any old unused code. Do not share this code.\n\n"
            "If you did not request it, change your password and contact support.\n"
        ),
    },
    TWO_FA_CODE: {
        "subject": "Your sign-in verification code",
        "html_body": (
            "<p>Hello,</p>"
            "<p>Use this code to complete verification. It is short-lived and should not be shared.</p>"
            "<p><strong>Code</strong> {{ code }}</p>"
            "<p>If you did not try to sign in, ignore this message and consider changing your password.</p>"
        ),
        "text_body": (
            "Your verification code: {{ code }}\n\n"
            "Do not share this code. If you did not request it, ignore this email and secure your account.\n"
        ),
    },
    GENERIC_NOTIFICATION: {
        "subject": "{{ title|default:'Notification' }}",
        "html_body": (
            "<p><strong>{{ title|default:'Notification' }}</strong></p>"
            "<p>{{ body }}</p>"
            "{% if action_url %}"
            "<p><a href=\"{{ action_url }}\">Open link</a></p>"
            "<p>{{ action_url }}</p>"
            "{% endif %}"
        ),
        "text_body": (
            "{{ title|default:'Notification' }}\n\n"
            "{{ body }}\n"
            "{% if action_url %}\nOpen: {{ action_url }}\n{% endif %}\n"
        ),
    },
}
