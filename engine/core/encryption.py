"""
Symmetric encryption helpers for storing sensitive credentials at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the ``cryptography`` library.
The encryption key is derived from ``settings.FIELD_ENCRYPTION_KEY`` if set,
otherwise falls back to ``settings.SECRET_KEY`` via PBKDF2.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    """Build a Fernet instance from the configured encryption key."""
    raw_key = getattr(settings, "FIELD_ENCRYPTION_KEY", None) or settings.SECRET_KEY
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        raw_key.encode(),
        b"field-encryption-salt",
        iterations=100_000,
    )
    url_safe_key = base64.urlsafe_b64encode(derived[:32])
    return Fernet(url_safe_key)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return a URL-safe base64 token."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet token back to plaintext."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return ""


def mask_value(plaintext: str) -> str:
    """Return a masked version of a value, showing only the last 4 chars."""
    if not plaintext:
        return ""
    if len(plaintext) <= 4:
        return "****"
    return "****" + plaintext[-4:]
