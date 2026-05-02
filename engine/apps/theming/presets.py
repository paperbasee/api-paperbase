"""
Single source of truth for storefront color palettes. Do not duplicate elsewhere.
"""

from __future__ import annotations

PALETTES: dict[str, dict[str, str]] = {
    "ivory": {
        "background": "#FAFAF8",
        "foreground": "#1A1A1A",
        "primary": "#1A1A1A",
        "primary_foreground": "#FAFAF8",
        "secondary": "#F0EFEA",
        "secondary_foreground": "#1A1A1A",
        "muted": "#F0EFEA",
        "muted_foreground": "#6B6B6B",
        "accent": "#C9A96E",
        "accent_foreground": "#1A1A1A",
        "card": "#F0EFEA",
        "card_foreground": "#1A1A1A",
        "popover": "#F0EFEA",
        "popover_foreground": "#1A1A1A",
        "border": "#E5E4DF",
        "input": "#E5E4DF",
        "ring": "#C9A96E",
        "header": "#1A1A1A",
        "header_foreground": "#FAFAF8",
    },
    "noir": {
        "background": "#000000",
        "foreground": "#FFFFFF",
        "primary": "#FFFFFF",
        "primary_foreground": "#000000",
        "secondary": "#111111",
        "secondary_foreground": "#FFFFFF",
        "muted": "#111111",
        "muted_foreground": "#888888",
        "accent": "#FFFFFF",
        "accent_foreground": "#000000",
        "card": "#111111",
        "card_foreground": "#FFFFFF",
        "popover": "#111111",
        "popover_foreground": "#FFFFFF",
        "border": "#222222",
        "input": "#222222",
        "ring": "#FFFFFF",
        "header": "#111111",
        "header_foreground": "#FFFFFF",
    },
    "arctic": {
        "background": "#F8FAFC",
        "foreground": "#0F172A",
        "primary": "#0F172A",
        "primary_foreground": "#F8FAFC",
        "secondary": "#F1F5F9",
        "secondary_foreground": "#0F172A",
        "muted": "#F1F5F9",
        "muted_foreground": "#64748B",
        "accent": "#3B82F6",
        "accent_foreground": "#F8FAFC",
        "card": "#F1F5F9",
        "card_foreground": "#0F172A",
        "popover": "#F1F5F9",
        "popover_foreground": "#0F172A",
        "border": "#E2E8F0",
        "input": "#E2E8F0",
        "ring": "#3B82F6",
        "header": "#0F172A",
        "header_foreground": "#F8FAFC",
    },
    "sage": {
        "background": "#F6F7F4",
        "foreground": "#2D3B2D",
        "primary": "#2D3B2D",
        "primary_foreground": "#F6F7F4",
        "secondary": "#ECEEE8",
        "secondary_foreground": "#2D3B2D",
        "muted": "#ECEEE8",
        "muted_foreground": "#6B7A6B",
        "accent": "#8FAF6E",
        "accent_foreground": "#2D3B2D",
        "card": "#ECEEE8",
        "card_foreground": "#2D3B2D",
        "popover": "#ECEEE8",
        "popover_foreground": "#2D3B2D",
        "border": "#DDE0D8",
        "input": "#DDE0D8",
        "ring": "#8FAF6E",
        "header": "#2D3B2D",
        "header_foreground": "#F6F7F4",
    },
}

DEFAULT_PALETTE = "ivory"
PALETTE_CHOICES = list(PALETTES.keys())

PALETTE_LABELS: dict[str, str] = {
    "ivory": "Ivory",
    "noir": "Noir",
    "arctic": "Arctic",
    "sage": "Sage",
}


def resolve_palette(palette_key: str) -> dict[str, str]:
    key = (palette_key or "").strip().lower()
    if key not in PALETTES:
        key = DEFAULT_PALETTE
    raw = PALETTES[key]
    return {k.replace("_", "-"): v for k, v in raw.items()}
