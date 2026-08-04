"""Registration, confirmation and leaving.

Confirmation goes to the Contact Email because a Kindle Address cannot receive
a message without an attachment and cannot practically be clicked from. See
docs/adr/0004-contact-email-alongside-kindle-address.md.
"""
from __future__ import annotations

import logging
import re
import secrets
from urllib.parse import quote

from wikindle.config import Settings
from wikindle.mail import Mailer, Message
from wikindle.models import Subscriber, SubscriberStatus
from wikindle.repository import Repository
from wikindle.tokens import unsubscribe_token

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

        # 24 bytes, not 32: the confirmation link has to survive quoted-printable
        # encoding, which folds plain-text lines at 76 characters. A longer token
        # pushes the URL over that and breaks it across a line, where many
        # clients will not linkify it and a copy-paste loses the tail.
        token = secrets.token_urlsafe(24)
        subscriber = self._repository.create_pending_subscriber(
            kindle_address, contact_email, timezone_name, token
        )

        self._mailer.send(
            Message(
                to=contact_email,
                subject="Confirm your wikindle subscription",
                text=self._confirmation_text(token, kindle_address),
                html=self._confirmation_html(token, kindle_address),
                reply_to=self._settings.sender_address,
                headers=self._list_headers(kindle_address),
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
                text=self._approved_sender_text(subscriber.kindle_address),
                html=self._approved_sender_html(subscriber.kindle_address),
                reply_to=self._settings.sender_address,
                headers=self._list_headers(subscriber.kindle_address),
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

    def _confirm_url(self, token: str) -> str:
        # Short query key on purpose; see the note where the token is generated.
        return f"{self._settings.public_url.rstrip('/')}/confirm?t={token}"

    def unsubscribe_url(self, kindle_address: str) -> str:
        token = unsubscribe_token(kindle_address, self._settings.secret_key)
        base = self._settings.public_url.rstrip("/")
        return f"{base}/unsubscribe?a={quote(kindle_address)}&t={token}"

    def _list_headers(self, kindle_address: str) -> dict[str, str]:
        """One-click unsubscribe, which mailbox providers treat as a good signal.

        Its absence is one of the things that lands a new domain's mail in the
        spam folder, and it is required of bulk senders regardless.
        """
        return {
            "List-Unsubscribe": f"<{self.unsubscribe_url(kindle_address)}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    def _confirmation_text(self, token: str, kindle_address: str) -> str:
        return (
            "Someone — hopefully you — asked wikindle to send one Wikipedia\n"
            f"article a day to the Kindle at {kindle_address}.\n\n"
            "Confirm that here:\n\n"
            f"{self._confirm_url(token)}\n\n"
            "The articles are drawn from Wikipedia's featured and good article\n"
            "pool, so they are proper written pieces rather than stubs, and they\n"
            "arrive as an EPUB your Kindle can lay out properly.\n\n"
            "If this was not you, ignore this message. Nothing will be sent and\n"
            "the address will be forgotten.\n\n"
            f"— wikindle, {self._settings.public_url}\n"
        )

    def _confirmation_html(self, token: str, kindle_address: str) -> str:
        link = self._confirm_url(token)
        return _wrap_html(
            f"""
            <p>Someone — hopefully you — asked wikindle to send one Wikipedia
            article a day to the Kindle at <strong>{kindle_address}</strong>.</p>
            <p><a class="button" href="{link}">Confirm my subscription</a></p>
            <p class="muted">Or paste this into your browser:<br>
            <a href="{link}">{link}</a></p>
            <p>The articles are drawn from Wikipedia's featured and good article
            pool, so they are proper written pieces rather than stubs, and they
            arrive as an EPUB your Kindle can lay out properly.</p>
            <p class="muted">If this was not you, ignore this message. Nothing
            will be sent and the address will be forgotten.</p>
            """
        )

    def _approved_sender_text(self, kindle_address: str) -> str:
        return (
            "You are subscribed. One step is left, and without it nothing will\n"
            "arrive at all.\n\n"
            "Amazon only accepts documents from addresses you have approved.\n"
            "Open your Personal Document Settings:\n\n"
            f"{self._settings.amazon_settings_url}\n\n"
            "On a non-US account, swap amazon.com in that link for your own —\n"
            "amazon.de, amazon.co.uk and so on. If it does not land in the right\n"
            "place: Account & Lists → Content and Devices → Preferences →\n"
            "Personal Document Settings.\n\n"
            "Add this address to your Approved Personal Document E-mail List:\n\n"
            f"    {self._settings.sender_address}\n\n"
            f"Until you do, everything we send to {kindle_address} is discarded\n"
            "silently. Amazon issues no bounce, so neither you nor we can tell\n"
            "that it happened — which is why we asked for this email address as\n"
            "well as your Kindle one.\n\n"
            f"To stop receiving articles: {self.unsubscribe_url(kindle_address)}\n"
        )

    def _approved_sender_html(self, kindle_address: str) -> str:
        return _wrap_html(
            f"""
            <p>You are subscribed. <strong>One step is left, and without it
            nothing will arrive at all.</strong></p>
            <p>Amazon only accepts documents from addresses you have approved.</p>
            <p><a class="button" href="{self._settings.amazon_settings_url}">Open
            your Personal Document Settings</a></p>
            <p class="muted">On a non-US account, swap <code>amazon.com</code> in
            that link for your own — <code>amazon.de</code>,
            <code>amazon.co.uk</code> and so on. If it does not land in the right
            place: Account &amp; Lists → Content and Devices → Preferences →
            Personal Document Settings.</p>
            <p>Add this address to your <em>Approved Personal Document E-mail
            List</em>:</p>
            <p><code>{self._settings.sender_address}</code></p>
            <p>Until you do, everything we send to <code>{kindle_address}</code>
            is discarded silently. Amazon issues no bounce, so neither you nor we
            can tell that it happened — which is why we asked for this email
            address as well as your Kindle one.</p>
            <p class="muted"><a href="{self.unsubscribe_url(kindle_address)}">Stop
            receiving articles</a></p>
            """
        )


def _wrap_html(body: str) -> str:
    """Minimal, inline-styled HTML — mail clients strip stylesheets."""
    return (
        '<!doctype html><html><body style="margin:0;padding:24px;'
        'font:16px/1.6 -apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'color:#18202a;background:#faf8f4">'
        '<div style="max-width:34rem;margin:0 auto">'
        f"{body}"
        "</div></body></html>"
    ).replace(
        'class="button"',
        'style="display:inline-block;padding:12px 20px;background:#3b3226;'
        'color:#fffdfa;text-decoration:none;border-radius:6px;font-weight:600"',
    ).replace(
        'class="muted"', 'style="color:#6d6455;font-size:14px"'
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
