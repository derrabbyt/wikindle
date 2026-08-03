"""Sending EPUBs, and telling people things their Kindle cannot tell them.

Amazon discards mail from senders absent from the reader's Approved Sender List
without any bounce, so a success here means "the provider accepted it", never
"somebody received it".
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

RESEND_ENDPOINT = "https://api.resend.com/emails"

#: Send to Kindle accepts 50 MB per message across at most 25 attachments;
#: Resend's own ceiling is 40 MB after base64. The lower of the two wins.
MAX_ATTACHMENT_BYTES = 40 * 1024 * 1024


class MailError(RuntimeError):
    """The provider would not accept the message."""


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    text: str
    attachment: Path | None = None
    attachment_name: str | None = None


class Mailer(Protocol):
    def send(self, message: Message) -> str:
        """Send *message*, returning the provider's id for it."""


@dataclass
class RecordingMailer:
    """A mailer that keeps messages instead of sending them.

    Used by the tests, and by local development where sending real mail to a
    real Kindle would be rude.
    """

    sent: list[Message] = field(default_factory=list)
    fail_for: set[str] = field(default_factory=set)

    def send(self, message: Message) -> str:
        if message.to in self.fail_for:
            raise MailError(f"refusing {message.to}")
        self.sent.append(message)
        return f"recorded-{len(self.sent)}"


class ResendMailer:
    def __init__(
        self,
        api_key: str,
        sender: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("a Resend API key is required")
        self._api_key = api_key
        self._sender = sender
        self._client = client or httpx.Client(timeout=timeout)

    def send(self, message: Message) -> str:
        payload: dict = {
            "from": self._sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }

        if message.attachment is not None:
            content = Path(message.attachment).read_bytes()
            encoded = base64.b64encode(content)
            if len(encoded) > MAX_ATTACHMENT_BYTES:
                raise MailError(
                    f"{message.attachment.name} is {len(encoded) // 1024 // 1024} MB "
                    f"encoded, over the {MAX_ATTACHMENT_BYTES // 1024 // 1024} MB limit"
                )
            payload["attachments"] = [
                {
                    "filename": message.attachment_name or message.attachment.name,
                    "content": encoded.decode("ascii"),
                }
            ]

        response = self._client.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if response.status_code >= 400:
            raise MailError(f"resend returned {response.status_code}: {response.text[:500]}")

        return response.json().get("id", "")
