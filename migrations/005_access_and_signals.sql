-- Customer links, security events and risk signals (Idempotent)

-- A claim link lets one customer open one decision. Only a hash of the link's secret is stored.
CREATE TABLE IF NOT EXISTS claim_links (
    claim_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    decision_id uuid NOT NULL REFERENCES decisions (decision_id),
    token_sha256 text NOT NULL UNIQUE,
    label text NOT NULL DEFAULT '',
    expires_at timestamptz NOT NULL,
    created_by_credential bigint,
    created_by_role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS claim_revocations (
    revocation_id bigserial PRIMARY KEY,
    claim_id bigint NOT NULL REFERENCES claim_links (claim_id),
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    revoked_by_role text NOT NULL,
    revoked_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE access_log ADD COLUMN IF NOT EXISTS claim_id bigint;

-- Platform-level events that cannot be tied to a lender yet (a wrong key, a bad link). Insert-only for the API.
CREATE TABLE IF NOT EXISTS security_events (
    event_id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('bad_key', 'bad_claim', 'expired_claim', 'blocked')),
    client text NOT NULL DEFAULT '',
    detail text NOT NULL DEFAULT '',
    at timestamptz NOT NULL DEFAULT now()
);

-- Signals worth a person's attention. They are indicators, never decisions.
CREATE TABLE IF NOT EXISTS risk_signals (
    signal_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    decision_id uuid REFERENCES decisions (decision_id),
    scope text NOT NULL CHECK (scope IN ('decision', 'batch', 'access')),
    signal_type text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info', 'warning', 'high')),
    message text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, signal_type, dedupe_key)
);

CREATE TABLE IF NOT EXISTS signal_reviews (
    review_id bigserial PRIMARY KEY,
    signal_id bigint NOT NULL REFERENCES risk_signals (signal_id),
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    action text NOT NULL CHECK (action IN ('acknowledged', 'cleared')),
    note text NOT NULL,
    reviewer_credential bigint,
    reviewer_role text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now()
);

-- A customer asking for a review is a request, not a change of result: keep it apart from lender overrides.
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'override'
    CHECK (kind IN ('override', 'review_request'));

-- A signal is looked at once.
CREATE UNIQUE INDEX IF NOT EXISTS uq_signal_reviews_signal ON signal_reviews (signal_id);

CREATE INDEX IF NOT EXISTS idx_signals_tenant ON risk_signals (tenant_id, signal_id DESC);
CREATE INDEX IF NOT EXISTS idx_signals_decision ON risk_signals (decision_id);
CREATE INDEX IF NOT EXISTS idx_access_log_actor ON access_log (tenant_id, credential_id, claim_id, logged_at DESC);

ALTER TABLE claim_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE claim_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_reviews ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['claim_links', 'claim_revocations', 'risk_signals', 'signal_reviews']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)) '
            'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))',
            t
        );
    END LOOP;
END
$$;

-- Looking a link up happens before the lender is known, so it goes through one narrow function instead of
-- opening the table: it returns only what is needed, for one exact hash.
CREATE OR REPLACE FUNCTION lookup_claim(h text)
RETURNS TABLE (claim_id bigint, tenant_id text, decision_id uuid, expires_at timestamptz, revoked boolean)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    SELECT c.claim_id, c.tenant_id, c.decision_id, c.expires_at,
           EXISTS (SELECT 1 FROM claim_revocations r WHERE r.claim_id = c.claim_id)
    FROM claim_links c JOIN tenants t ON t.tenant_id = c.tenant_id
    WHERE c.token_sha256 = h AND t.status = 'active'
$$;
REVOKE ALL ON FUNCTION lookup_claim(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lookup_claim(text) TO app_role;

REVOKE ALL ON TABLE claim_links, claim_revocations, security_events, risk_signals, signal_reviews FROM app_role, PUBLIC;
GRANT SELECT, INSERT ON TABLE claim_links, claim_revocations, risk_signals, signal_reviews TO app_role;
GRANT INSERT ON TABLE security_events TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
