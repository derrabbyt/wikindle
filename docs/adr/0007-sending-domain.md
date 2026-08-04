---
status: accepted
---

# The sending address is read@send.wikindle.xyz, permanently

The site is `wikindle.xyz`, the API is reached at `api.wikindle.xyz` through the
tunnel, and mail is sent from `read@send.wikindle.xyz`. Sending lives on its own
subdomain, as Resend recommends, so that anything else ever sent from the root
domain — or any abuse of a form on it — cannot degrade the reputation of the
mail this product exists to deliver.

**This address can never change.** Every Subscriber types it by hand into their
Amazon Approved Personal Document E-mail List, and Amazon then accepts documents
only from exactly that string. Changing it does not produce errors; it produces
an audience that quietly stops receiving anything. The mail provider is *not*
locked in by this — Resend can be swapped for SES without touching a reader — but
the address and the domain are.

## Consequences

The subdomain makes the address longer, and `send.` is the kind of thing a
person assumes is a mistake and drops, leaving them with an address that will
never work and no error to explain why. Two mitigations, both cheap: the local
part is kept short, and the confirmation page offers the address as
copy-to-clipboard rather than something to retype.

## The name

"Kindle" appears in the domain, which Amazon's brand guidelines discourage for
third-party products. The risk was raised, weighed and accepted: enforcement
against a hobby project of this size is unlikely, the domain is inexpensive, and
the whole system reads its sending address from configuration, so moving to a
different domain is a settings change plus the unavoidable cost of asking every
existing Subscriber to update their approved list.
