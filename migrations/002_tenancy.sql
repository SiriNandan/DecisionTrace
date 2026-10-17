-- DecisionTrace tenancy, credentials and access log (Idempotent)
-- Every evidence table gets a tenant_id; row-level security restricts app_role to the tenant named in the
-- session setting app.tenant_id. Superusers and table owners bypass RLS, so the API must connect as app_role.

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id text PRIMARY KEY CHECK (tenant_id ~ '^[a-z0-9][a-z0-9_-]{1,62}$'),
    display_name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO tenants (tenant_id, display_name) VALUES ('demo', 'Demo lender (synthetic data)')
ON CONFLICT (tenant_id) DO NOTHING;

-- Only a SHA-256 of each key is stored; the key itself is shown once when issued.
CREATE TABLE IF NOT EXISTS api_credentials (
    credential_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id),
    key_sha256 text NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('lender_ingest', 'lender_analyst', 'auditor', 'admin')),
    label text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);

-- Rows written without an explicit tenant take the session's tenant; seed/maintenance sessions fall back to 'demo'.
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL
    DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo') REFERENCES tenants (tenant_id);
ALTER TABLE decision_snapshots ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL
    DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo') REFERENCES tenants (tenant_id);
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL
    DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo') REFERENCES tenants (tenant_id);
ALTER TABLE evidence_status ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL
    DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo') REFERENCES tenants (tenant_id);

-- 'scored' = DecisionTrace computed the decision (demo mode); 'recorded' = the lender's engine decided and sent it.
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS recording_mode text NOT NULL DEFAULT 'scored'
    CHECK (recording_mode IN ('scored', 'recorded'));

CREATE INDEX IF NOT EXISTS idx_decisions_tenant_decided ON decisions (tenant_id, decided_at DESC);

CREATE TABLE IF NOT EXISTS access_log (
    log_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id),
    credential_id bigint,
    role text NOT NULL,
    action text NOT NULL,
    object_type text NOT NULL,
    object_id text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('allowed', 'not_found')),
    logged_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_access_log_tenant_time ON access_log (tenant_id, logged_at DESC);

-- Row-level security
ALTER TABLE decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_log ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['decisions', 'decision_snapshots', 'overrides', 'evidence_status', 'access_log']
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

-- Least privilege for the API role: credentials and tenants are read-only, the access log is append-only.
REVOKE ALL ON TABLE tenants, api_credentials, access_log FROM app_role, PUBLIC;
GRANT SELECT ON TABLE tenants, api_credentials TO app_role;
GRANT SELECT, INSERT ON TABLE access_log TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
