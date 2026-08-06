-- Drop the confirmation step and the Contact Email.
--
-- schema.sql only runs when PostgreSQL initialises an empty data directory, so
-- an existing deployment needs this applied by hand:
--
--     docker compose exec -T db psql -U wikindle -d wikindle \
--         < db/migrations/0001-drop-confirmation.sql
--
-- See docs/adr/0008-no-confirmation-step.md.

BEGIN;

-- Anyone mid-signup is simply subscribed now; there is nothing left to confirm.
UPDATE subscribers SET status = 'active' WHERE status = 'pending';

ALTER TABLE subscribers DROP CONSTRAINT IF EXISTS subscribers_status_check;
ALTER TABLE subscribers
    ADD CONSTRAINT subscribers_status_check
    CHECK (status IN ('active', 'unsubscribed'));

ALTER TABLE subscribers DROP COLUMN IF EXISTS contact_email;
ALTER TABLE subscribers DROP COLUMN IF EXISTS confirm_token;
ALTER TABLE subscribers DROP COLUMN IF EXISTS confirmed_at;

COMMIT;
