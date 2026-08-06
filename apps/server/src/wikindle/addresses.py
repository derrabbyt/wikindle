"""Normalising email addresses, carefully.

The local part of an address is case-sensitive by RFC 5321 §2.3.11 — only the
receiving server may interpret it. Amazon relies on that: a Send to Kindle
address carries a random mixed-case suffix, so lowercasing one yields an address
that does not exist. Mail to it is discarded with no bounce, which makes the
mistake invisible until somebody notices they never receive anything.

Only the domain, which *is* case-insensitive, gets lowercased.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Amazon hands out addresses on kindle.com and its subdomains; anything else is
#: a typo that would fail silently days later.
_KINDLE_DOMAIN = re.compile(r"(^|\.)kindle\.com$", re.I)


def normalise_email(value: str, what: str = "email address") -> str:
    address = (value or "").strip()
    if not _EMAIL.match(address):
        raise ValueError(f"{address!r} is not a valid {what}")

    local, _, domain = address.rpartition("@")
    return f"{local}@{domain.lower()}"


def normalise_kindle_address(value: str) -> str:
    address = normalise_email(value, "Kindle address")
    if not _KINDLE_DOMAIN.search(address.rpartition("@")[2]):
        raise ValueError(
            f"{address!r} is not a Send to Kindle address — it should end in "
            "@kindle.com, and is not the same as your Amazon account email"
        )
    return address
