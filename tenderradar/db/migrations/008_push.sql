-- 008_push.sql — Web Push subscriptions (§4 Phase 2).
--
-- Web push is free and unlimited, which is exactly why it is in Phase 2 while
-- SMS is a named cost trap. It works well on Android Chrome, which is what
-- this market carries.
--
-- One user may have several subscriptions: phone, office desktop, a second
-- browser. Each endpoint is its own row, and the endpoint URL is the identity
-- assigned by the browser's push service.

CREATE TABLE push_subscriptions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    endpoint        TEXT        NOT NULL UNIQUE,
    p256dh          TEXT        NOT NULL,
    auth            TEXT        NOT NULL,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_success_at TIMESTAMPTZ,
    -- A push service returns 404 or 410 when a subscription is dead. Keeping
    -- the row and marking it retired preserves the history without retrying a
    -- gone endpoint forever.
    retired_at      TIMESTAMPTZ,
    failure_count   INTEGER     NOT NULL DEFAULT 0,
    last_error      TEXT
);

CREATE INDEX push_subscriptions_user_idx
    ON push_subscriptions (user_id) WHERE retired_at IS NULL;
