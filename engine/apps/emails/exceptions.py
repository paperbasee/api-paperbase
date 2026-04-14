"""Email subsystem exceptions."""


class SecurityError(Exception):
    """Raised when tenant or security invariants are violated (e.g. async email task mismatch)."""
