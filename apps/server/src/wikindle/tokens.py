"""Stateless one-click unsubscribe tokens.

Derived from the Kindle Address with an HMAC rather than stored, so there is no
column to migrate and no row to clean up. They do not expire: an unsubscribe
link in a two-year-old email should still work, and the worst it can do is
unsubscribe the address it was issued for.
"""
from __future__ import annotations

import hmac
from hashlib import sha256


def unsubscribe_token(address: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), address.strip().lower().encode(), sha256)
    return digest.hexdigest()[:32]


def verify_unsubscribe_token(address: str, token: str, secret: str) -> bool:
    expected = unsubscribe_token(address, secret)
    return hmac.compare_digest(expected, (token or "").strip())
