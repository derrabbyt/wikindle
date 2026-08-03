# Backend runs on a self-hosted VPS behind a Cloudflare Tunnel

The converter is Python and shells out to pandoc and ImageMagick, none of which
can run in a Cloudflare Worker, so the runtime had to be decided before anything
else could be built. We run the API, PostgreSQL, the converter, the scheduler and
the mailer on a single VPS, reached through `cloudflared` so the box accepts no
inbound connections at all; Cloudflare Pages continues to serve the Angular SPA
as free static assets.

## Considered options

The repository contains a fully working Cloudflare Worker, Hyperdrive binding and
Neon database from the demo this project grew out of. All three are deliberately
decommissioned. Cloudflare Containers was the closest alternative and would have
kept everything on one platform for $5/month with room to spare, but a single box
is the less surprising system and gives one stable egress IP — which matters
because Wikimedia rate-limits image fetches aggressively, and shared cloud egress
ranges are treated worse than a dedicated address.

## Consequences

The database password no longer lives only in Hyperdrive — the box holds its own
credentials — and we now own PostgreSQL backups that Neon was doing invisibly.
Off-box backups are not optional for a table of subscribers' email addresses.
