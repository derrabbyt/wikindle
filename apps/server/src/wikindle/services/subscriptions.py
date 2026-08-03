"""Registration, confirmation and leaving.

Confirmation goes to the Contact Email because a Kindle Address cannot receive
a message without an attachment and cannot practically be clicked from. See
docs/adr/0004-contact-email-alongside-kindle-address.md.
"""
from __future__ import annotations

import logging
import re
import secrets

from wikindle.config import Settings
from wikindle.mail import Mailer, Message
from wikindle.models import Subscriber, SubscriberStatus
from wikindle.repository import Repository

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Amazon hands out addresses on kindle.com; anything else is a typo that would
#: fail silently days later.
_KINDLE_DOMAIN = re.compile(r"(^|\.)kindle\.com$", re.I)


class AlreadyActive(ValueError):
    """This Kindle Address is already confirmed."""


class SubscriptionService:
    def __init__(
        self, repository: Repository, settings: Settings, mailer: Mailer
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._mailer = mailer

    def register(
        self, kindle_address: str, contact_email: str, timezone_name: str | None
    ) -> Subscriber:
        kindle_address = _normalise_kindle_address(kindle_address)
        contact_email = _normalise_email(contact_email, "contact email")

        existing = self._repository.subscriber_by_kindle_address(kindle_address)
        if existing and existing.status is SubscriberStatus.ACTIVE:
            raise AlreadyActive(f"{kindle_address} is already subscribed")

        token = secrets.token_urlsafe(32)
        subscriber = self._repository.create_pending_subscriber(
            kindle_address, contact_email, timezone_name, token
        )

        self._mailer.send(
            Message(
                to=contact_email,
                subject="Confirm your wikindle subscription",
                text=self._confirmation_body(token, kindle_address),
            )
        )
        return subscriber

    def confirm(self, token: str) -> Subscriber:
        subscriber = self._repository.subscriber_by_token(token)
        if subscriber is None:
            raise LookupError("unknown or already-used confirmation link")

        self._repository.activate_subscriber(subscriber.id)
        confirmed = self._repository.subscriber_by_kindle_address(
            subscriber.kindle_address
        )

        self._mailer.send(
            Message(
                to=subscriber.contact_email,
                subject="One more step: approve wikindle on your Kindle",
                text=self._approved_sender_body(subscriber.kindle_address),
            )
        )
        return confirmed

    def unsubscribe(self, kindle_address: str) -> None:
        subscriber = self._repository.subscriber_by_kindle_address(
            _normalise_kindle_address(kindle_address)
        )
        if subscriber is None:
            raise LookupError("not subscribed")
        self._repository.unsubscribe(subscriber.id)

    def delete(self, kindle_address: str) -> None:
        subscriber = self._repository.subscriber_by_kindle_address(
            _normalise_kindle_address(kindle_address)
        )
        if subscriber is None:
            raise LookupError("not subscribed")
        self._repository.delete_subscriber(subscriber.id)

    # -- copy -------------------------------------------------------------

    def _confirmation_body(self, token: str, kindle_address: str) -> str:
        link = f"{self._settings.public_url.rstrip('/')}/confirm?token={token}"
        return (
            "Confirm that you would like a Wikipedia article delivered to\n"
            f"{kindle_address} every day:\n\n"
            f"{link}\n\n"
            "If you did not request this, ignore this message and nothing will "
            "be sent.\n"
        )

    def _approved_sender_body(self, kindle_address: str) -> str:
        return (
            "You are subscribed. There is one step left, and without it nothing "
            "will arrive.\n\n"
            "Amazon only accepts documents from addresses you have approved. Open\n"
            "Amazon → Account → Content and Devices → Preferences → Personal\n"
            "Document Settings, and add this address to your Approved Personal\n"
            f"Document E-mail List:\n\n    {self._settings.sender_address}\n\n"
            f"Until you do, mail to {kindle_address} is discarded silently — we "
            "get no bounce and cannot tell that it happened.\n"
        )


def _normalise_email(value: str, what: str) -> str:
    value = (value or "").strip().lower()
    if not _EMAIL.match(value):
        raise ValueError(f"{value!r} is not a valid {what}")
    return value


def _normalise_kindle_address(value: str) -> str:
    address = _normalise_email(value, "Kindle address")
    if not _KINDLE_DOMAIN.search(address.split("@", 1)[1]):
        raise ValueError(
            f"{address!r} is not a Send to Kindle address — it should end in "
            "@kindle.com, and is not the same as your Amazon account email"
        )
    return address
