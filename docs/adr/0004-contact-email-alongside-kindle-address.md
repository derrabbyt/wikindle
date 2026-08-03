# Signup collects a Contact Email as well as a Kindle Address

Send to Kindle refuses any email without an attachment, and what it does deliver
arrives as a document in the reader's library on a device whose browser is barely
usable — so a "click here to confirm" email cannot be sent to a Kindle Address at
all. Double opt-in therefore requires a second, ordinary mailbox, which we collect
at signup and use for confirmation and problem notices only.

The stronger reason is that Amazon discards mail from senders absent from a
reader's Approved Personal Document E-mail List **silently**, with no bounce and
no error. Our delivery log will record `sent` for someone who has received
nothing and never will. The Contact Email is the only channel through which such
a Subscriber can ever be told, and it cannot be added retroactively — asking
existing Subscribers for one would require a channel we would not have.

The cost is a two-field signup form and twice the personal data held.
