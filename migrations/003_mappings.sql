-- Field mappings: how a lender's own file layout maps onto the decision envelope (Idempotent)
-- Rows are never edited. A change is a new version; approval and rejection are separate rows in mapping_reviews.

CREATE TABLE IF NOT EXISTS mappings (
    mapping_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    version int NOT NULL CHECK (version >= 1),
    spec jsonb NOT NULL,
    spec_sha256 text NOT NULL,
    proposed_by text NOT NULL,
    created_by_credential bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name, version)
);

CREATE TABLE IF NOT EXISTS mapping_reviews (
    review_id bigserial PRIMARY KEY,
    mapping_id bigint NOT NULL REFERENCES mappings (mapping_id),
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    action text NOT NULL CHECK (action IN ('approved', 'rejected')),
    note text NOT NULL DEFAULT '',
    reviewer_credential bigint,
    reviewer_role text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE mapping_reviews ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['mappings', 'mapping_reviews']
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

REVOKE ALL ON TABLE mappings, mapping_reviews FROM app_role, PUBLIC;
GRANT SELECT, INSERT ON TABLE mappings, mapping_reviews TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
