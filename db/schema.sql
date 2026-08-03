-- wikindle schema.
--
-- A Conversion is one EPUB built from one Article and is reusable by anyone
-- forever. An Edition is the separate, thin fact that a Conversion was the
-- article of the day on a given date. See
-- docs/adr/0002-conversion-and-edition-are-distinct.md.

BEGIN;

-- ---------------------------------------------------------------- article pool
-- Synced weekly from the wiki's featured and good article categories, so that
-- choosing the day's Article is a local query and the daily run does not depend
-- on Wikipedia being reachable.
CREATE TABLE IF NOT EXISTS pool_articles (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_url   TEXT        NOT NULL UNIQUE,
    title        TEXT        NOT NULL,
    language     TEXT        NOT NULL,
    quality      TEXT        NOT NULL CHECK (quality IN ('featured', 'good')),
    synced_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pool_articles_language_idx ON pool_articles (language);

-- ------------------------------------------------------------------ conversions
CREATE TABLE IF NOT EXISTS conversions (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_url        TEXT        NOT NULL,
    converter_version TEXT        NOT NULL,
    title             TEXT,
    language          TEXT,
    status            TEXT        NOT NULL DEFAULT 'building'
                                  CHECK (status IN ('building', 'built', 'failed')),
    epub_path         TEXT,
    epub_bytes        INTEGER,
    word_count        INTEGER,
    images_kept       INTEGER,
    images_missing    INTEGER,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    built_at          TIMESTAMPTZ,

    -- The cache key. Bumping the converter version rebuilds rather than serving
    -- an EPUB made by code that has since been fixed.
    UNIQUE (source_url, converter_version)
);

CREATE INDEX IF NOT EXISTS conversions_reusable_idx
    ON conversions (source_url, converter_version)
    WHERE status = 'built';

-- --------------------------------------------------------------------- editions
-- One row per date, holding the Conversion everyone receives that day. Built
-- ahead of the send so a failure has somewhere to be noticed.
CREATE TABLE IF NOT EXISTS editions (
    edition_date  DATE PRIMARY KEY,
    conversion_id BIGINT      NOT NULL REFERENCES conversions (id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ subscribers
-- A Kindle Address cannot receive an email without an attachment and cannot
-- practically be clicked from, so confirmation goes to the Contact Email. See
-- docs/adr/0004-contact-email-alongside-kindle-address.md.
CREATE TABLE IF NOT EXISTS subscribers (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kindle_address  TEXT        NOT NULL UNIQUE,
    contact_email   TEXT        NOT NULL,
    timezone        TEXT,
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'active', 'unsubscribed')),
    confirm_token   TEXT UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at    TIMESTAMPTZ,
    unsubscribed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS subscribers_active_idx
    ON subscribers (id) WHERE status = 'active';

-- ------------------------------------------------------------------- deliveries
CREATE TABLE IF NOT EXISTS deliveries (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscriber_id       BIGINT      NOT NULL REFERENCES subscribers (id) ON DELETE CASCADE,
    conversion_id       BIGINT      NOT NULL REFERENCES conversions (id),
    edition_date        DATE        REFERENCES editions (edition_date),
    kind                TEXT        NOT NULL
                                    CHECK (kind IN ('daily', 'welcome', 'on_demand')),
    status              TEXT        NOT NULL DEFAULT 'queued'
                                    CHECK (status IN ('queued', 'sent', 'failed')),
    error               TEXT,
    provider_message_id TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at             TIMESTAMPTZ
);

-- The daily run must be safe to re-run: a partial fan-out that is retried has
-- to skip whoever already received that date's Edition.
CREATE UNIQUE INDEX IF NOT EXISTS deliveries_one_per_edition_idx
    ON deliveries (subscriber_id, edition_date)
    WHERE edition_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS deliveries_rate_limit_idx
    ON deliveries (subscriber_id, kind, created_at);

-- Amazon discards mail from senders absent from a reader's Approved Sender List
-- without any bounce, so a row here saying 'sent' is evidence that we handed the
-- message to Resend — never that anybody received it.
COMMENT ON COLUMN deliveries.status IS
    'queued|sent|failed — sent means accepted by the mail provider, not delivered';

COMMIT;
