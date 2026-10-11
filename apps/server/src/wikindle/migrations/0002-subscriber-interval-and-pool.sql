-- Add the per-Subscriber delivery interval and article pool.

-- Existing Subscribers keep what they had: one featured article a day.
ALTER TABLE subscribers
    ADD COLUMN IF NOT EXISTS interval_days INTEGER NOT NULL DEFAULT 1
    CHECK (interval_days >= 1 AND interval_days <= 30);

ALTER TABLE subscribers
    ADD COLUMN IF NOT EXISTS article_pool TEXT NOT NULL DEFAULT 'featured'
    CHECK (article_pool IN ('featured', 'good', 'unusual_articles', 'did_you_know', 'vital_articles', 'common_misconceptions', 'featured_articles'));
