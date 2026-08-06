---
status: accepted
supersedes: ADR-0004
---

# There is no confirmation step, and no Contact Email

Signup asks for a Kindle Address and nothing else, and takes effect
immediately. ADR 0004's double opt-in is removed.

## Why the old reasoning was wrong

ADR 0004 justified collecting a second, ordinary mailbox on the grounds that a
Kindle Address cannot receive a confirmation link, so double opt-in needed
somewhere to send one.

That confirmation never protected anybody. Clicking a link in a mailbox proves
control **of that mailbox** — it says nothing about the Kindle. Anyone wanting
to subscribe a stranger's device only had to supply their own email address and
click their own link. As an anti-abuse mechanism it was theatre.

The real gate was always Amazon's Approved Personal Document E-mail List:
nothing can be delivered to a Kindle until its owner has added our sending
address by hand, and that is enforced by Amazon rather than by us. It is a
stronger consent signal than a clicked link, and we cannot bypass it even if we
wanted to.

## What is lost

The rescue channel. ADR 0004's better argument was that a Contact Email is the
only way to tell somebody their deliveries are being silently discarded. That
is genuinely gone: **wikindle now holds no address a human reads.** Everything a
Subscriber needs to know has to be said on the website while they are looking at
it, which is why the signup response carries the approved-sender instruction and
why there is a separate help page.

Tracking failed sends does not replace it, and it is worth being precise about
why. Amazon accepts the SMTP transaction for an unapproved sender and *then*
discards the document. There is no bounce and no webhook — the exact failure we
would most want to detect is the one that leaves no trace. What a bounce does
reveal is a Kindle Address that does not exist at all, which is a typo rather
than a missing approval.

## Consequences

Unsubscribing is by Kindle Address on the website, consistent with
ADR 0005 — anyone who knows the address could already cause sends to it, and
stopping them is the less harmful of the two. A Subscriber also has a kill
switch we cannot override: removing our address from their approved list stops
delivery instantly.

`db/migrations/0001-drop-confirmation.sql` applies this to an existing database.
