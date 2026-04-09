"""
Customer profile/address-book endpoints were removed as part of the strict minimal
Customer schema refactor. Customer history is derived from orders and the immutable
purchase ledger; the `customers` table remains identity + rollups only.
"""

# Intentionally empty: this app no longer exposes non-admin customer endpoints.
