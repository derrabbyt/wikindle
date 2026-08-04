---
status: accepted
---

# The sending address is read@wikindle.xyz, permanently

The site is `wikindle.xyz`, the API is reached at `api.wikindle.xyz` through the
tunnel, and mail is sent from `read@wikindle.xyz` — from the apex, not a
dedicated sending subdomain.

**This address can never change.** Every Subscriber types it by hand into their
Amazon Approved Personal Document E-mail List, and Amazon then accepts documents
only from exactly that string. Changing it produces no errors; it produces an
audience that quietly stops receiving anything. The mail provider is *not* locked
in — Resend can be swapped for SES without touching a reader — but the address is.

## Why not a sending subdomain

Resend recommends `send.wikindle.xyz`, and for most products that is right: it
isolates sending reputation, so anything else ever sent from the root domain
cannot degrade the mail that matters.

It was tried and reversed. This product has an unusual constraint — the address
is retyped by hand into a settings page, and a typo fails *silently and
permanently*, because Amazon simply never accepts our mail and neither party
receives an error. `send.` reads like a mistake and is exactly the sort of thing
a person drops. Against a concrete, recurring failure mode, isolating reputation
defends a risk that a domain sending nothing but transactional mail may never
face.

The confirmation page still offers the address as copy-to-clipboard rather than
something to retype; that mitigation is worth keeping regardless.

## Consequences

SPF, DKIM and DMARC now live on the apex, which means anything else sent from
`wikindle.xyz` later shares this reputation. Resend's bounce-feedback `MX` also
sits on the apex, so it will collide with any mailbox provider if the domain is
ever used to *receive* mail — `hello@wikindle.xyz` would need that resolved
first.

## The name

"Kindle" appears in the domain, which Amazon's brand guidelines discourage for
third-party products. The risk was raised, weighed and accepted: enforcement
against a hobby project of this size is unlikely, the domain is inexpensive, and
the sending address is read from configuration — so moving is a settings change
plus the unavoidable cost of asking every existing Subscriber to update their
approved list.
