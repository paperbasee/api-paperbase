from __future__ import annotations

from typing import Final


ALLOWED_EVENT_NAMES: Final[set[str]] = {
    "PageView",
    "ViewContent",
    "AddToCart",
    "InitiateCheckout",
    "Purchase",
}

