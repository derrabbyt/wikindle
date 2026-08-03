# wikindle

One Wikipedia article a day, delivered to your Kindle as a properly formatted
EPUB. Subscribers can also pull an extra article on demand — at random, or from
any Wikipedia link they paste.

Delivery uses Amazon's **Send to Kindle** personal-document email: the `.epub`
is mailed as an attachment to the reader's `@kindle.com` address.

The vocabulary this project uses — Article, Conversion, Edition, Subscriber,
Kindle Address, Contact Email, Delivery — is defined in [CONTEXT.md](CONTEXT.md).
The decisions that shaped it are in [docs/adr/](docs/adr/).

## Shape

```text
Cloudflare Pages (static SPA)
        │  HTTPS
        ▼
Cloudflare Tunnel  ── outbound only, no inbound ports on the box
        │
        ▼
VPS ─ FastAPI · PostgreSQL · converter · scheduler · Resend
```

One box runs everything behind a tunnel it dials out to, so it never accepts an
inbound connection from the internet. See
[ADR 0001](docs/adr/0001-self-hosted-vps-behind-cloudflare-tunnel.md).

| Path | What |
|---|---|
| `apps/server/` | Python: API, converter, jobs. The whole backend. |
| `apps/web/` | Angular SPA, deployed to Cloudflare Pages. |
| `db/schema.sql` | The database, applied on first start. |
| `docs/adr/` | Why things are the way they are. |

## Running it

```bash
cp .env.example .env      # then fill it in
docker compose up -d
```

The image pins pandoc 3.10.1 deliberately — Debian ships a version too old to
build these EPUBs at all. See
[ADR 0006](docs/adr/0006-docker-image-pins-pandoc.md).

Once up, seed the article pool. It is drawn from the wiki's featured and good
article categories — 51,535 titles on the English Wikipedia, about 141 years of
daily Editions — so that choosing an article is a local query and the daily run
does not depend on Wikipedia being reachable at four in the morning:

```bash
docker compose exec api python -m wikindle sync-pool
```

The scheduler then builds each Edition the evening before and sends it at 04:00
UTC. The jobs can also be run by hand:

```bash
docker compose exec api python -m wikindle build          # top up the buffer
docker compose exec api python -m wikindle send           # fan out today's
docker compose exec api python -m wikindle send --date 2026-08-03
```

Backups are yours to run — nothing else does them:

```bash
docker compose run --rm backup
```

## Developing

```bash
cd apps/server
pip install -e ".[dev]"
pytest
```

The suite runs without a database. To exercise the real SQL as well:

```bash
docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=x --name pg postgres:17-alpine
WIKINDLE_TEST_DATABASE_URL=postgresql://postgres:x@localhost:55432/postgres pytest
```

Tests that reach the live internet are marked `network` and excluded by default;
run them with `pytest -m network`.

## The one thing readers must do themselves

Amazon only accepts personal documents from addresses on the reader's **Approved
Personal Document E-mail List**, which only they can edit. Until wikindle's
sending address is on it, Amazon discards our mail **with no bounce and no
error** — a delivery recorded as `sent` is evidence that Resend accepted the
message, never that anyone received it. This is why signup asks for an ordinary
email address as well as a Kindle Address: it is the only channel through which
a silent subscriber can be reached. See
[ADR 0004](docs/adr/0004-contact-email-alongside-kindle-address.md).

## Licence and attribution

Article text comes from Wikipedia under CC BY-SA 4.0, and each EPUB carries the
licence and its source URL.
