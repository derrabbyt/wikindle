---
status: accepted
---

# On-demand sends are not authenticated

There are no passwords and no sessions: a Kindle Address is the whole identity.
On-demand conversion requires only that the submitted address belongs to a
confirmed Subscriber, so anyone who knows such an address can cause articles to
appear on that device. This risk is accepted knowingly rather than overlooked.

The blast radius is narrow — a send can only ever go *to* the address it was
requested for, so this is nuisance, not exfiltration. The alternative considered
was a signed per-Subscriber capability link carried in every email, reusing the
token machinery that unsubscribe needs anyway; it was rejected for v1 as more
machinery than the risk warrants.

Rate limits per address and per IP still apply, but they exist to protect the
Resend send quota and to be polite to Wikimedia — not as a security control.
