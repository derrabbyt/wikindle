# Wikindle

Delivers one Wikipedia article a day to a reader's Kindle as a formatted EPUB,
using Amazon's Send to Kindle personal-document email. Readers can also request
an extra article on demand, either at random or from a link they supply.

## Language

### Content

**Article**:
A Wikipedia page, identified by its URL. Always the source, never anything we
produce.
_Avoid_: page, entry, document

**Conversion**:
One EPUB built from one Article by one version of the converter. Belongs to the
Article, not to any Subscriber, so the same Conversion can be sent to many people
and reused on later days.
_Avoid_: build, artifact, edition, epub

**Edition**:
The designation of a Conversion as *the article of the day* for a given date.
Exactly one per date, and only the daily run creates one. An on-demand send has
no Edition.
_Avoid_: issue, daily, batch

### People and delivery

**Subscriber**:
A Kindle Address signed up to receive Editions. There is no pending state and
nothing to confirm — see docs/adr/0008-no-confirmation-step.md.
_Avoid_: user, customer, account. ("Reader" is fine in interface copy, but not in
code or documentation.)

**Kindle Address**:
The `@kindle.com` address a Subscriber's device receives personal documents at.
Deliberately distinct from the Amazon account email — they are different
addresses, and confusing them is the most likely mistake a Subscriber makes.
Receives Conversions and nothing else. It is the only address
wikindle holds, and no human reads it.
_Avoid_: email, address

**Approved Sender List**:
The list, held by Amazon and editable only by the Subscriber, of addresses
permitted to send personal documents to their Kindle Address. If wikindle's
sending address is absent from it, Amazon discards the mail without any bounce or
error, so we cannot detect this failure.
_Avoid_: whitelist, allowed senders

**Delivery**:
One attempt to send one Conversion to one Subscriber. Records what was sent and
what happened, so a repeated run does not send the same thing twice.
_Avoid_: send, dispatch, email
