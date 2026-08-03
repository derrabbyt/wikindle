# Conversion and Edition are separate concepts

An EPUB can now be created three ways — the daily article, an on-demand random
article, and a link a Subscriber pastes — and a single table keyed by date cannot
represent that. A Conversion is one EPUB built from one Article, keyed by source
URL and reusable by anyone forever; an Edition is the separate, thin fact that a
particular Conversion was the article of the day on a particular date.

## Consequences

Deliveries reference the Conversion that was actually sent, with a nullable
edition reference recording whether it was part of a daily fan-out. A Subscriber
who pastes a link somebody already converted is served from cache instantly,
without a conversion and without re-fetching anything from Wikimedia.

An earlier draft modelled this as a single `editions` table whose `edition_date`
was `DATE NOT NULL UNIQUE`, while also intending on-demand rows to be untied to
any date. Those are contradictory: the constraint permits exactly one non-daily
row in the lifetime of the system. Fusing the two concepts is what forced the
column to be simultaneously mandatory and meaningless.
