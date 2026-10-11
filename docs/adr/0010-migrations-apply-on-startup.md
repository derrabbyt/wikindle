---
status: accepted
---

# Migrations ship in the image and apply on startup

The api and the scheduler apply any pending migration before they do anything
else. Migrations live in the server package, so an image always carries the
schema changes its own code needs.

## Why

`db/schema.sql` only runs when PostgreSQL initialises an empty volume, so an
existing database was changed by piping a file into `psql` on the box. That was
tolerable while every release was rolled out by hand. Once CI started rolling
out by itself (ADR 0009), the first commit to add a column went live without it,
leaving signups writing to a column production did not have, and nothing had
gone red.

## How

A migration is a `.sql` file in `apps/server/src/wikindle/migrations/`, applied
in filename order, each in one transaction together with a row in
`schema_migrations` that records it. An advisory lock keeps the two services
from migrating at once.

A service whose migration fails does not start. That is deliberate: the API
being down is visible — the rollout's health check fails — where an API serving
against the wrong schema was not.

## Consequences

`db/schema.sql` remains the definition of a fresh database, so a schema change
is written twice: once there, once as a migration. A fresh deployment gets the
schema and then every migration on top of it, which means **a migration has to
be harmless when its change is already present** — `IF EXISTS` and
`IF NOT EXISTS` throughout. A test applies every migration to a fresh schema to
hold that line.

There is no down-migration. Rolling back means deploying an older image against
the newer schema, so a migration should only add things until nothing running
still depends on what it would remove.
