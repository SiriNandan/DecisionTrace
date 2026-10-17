-- Protected evidence: per-decision data keys, wrapped by a per-lender key; erasing a decision destroys its key (Idempotent)
-- The wrapped key is the only way to read an encrypted record. Destroying it (as the owner role) makes the content
-- unreadable while the stored fingerprints, and so the hash chain and seals, stay exactly as they were.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS encrypt_evidence boolean NOT NULL DEFAULT false;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS encrypted boolean NOT NULL DEFAULT false;

-- Data-quality findings about a protected decision keep their wording and values encrypted too.
ALTER TABLE quality_findings ADD COLUMN IF NOT EXISTS enc_detail jsonb;

CREATE TABLE IF NOT EXISTS data_key_wraps (
    wrap_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    decision_id uuid NOT NULL REFERENCES decisions (decision_id),
    kek_version int NOT NULL CHECK (kek_version >= 1),
    wrapped_dek bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (decision_id, kek_version)
);

-- A record that a decision's key was destroyed, and why. The API can read this; only the owner role writes it.
CREATE TABLE IF NOT EXISTS erasures (
    erasure_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id),
    decision_id uuid NOT NULL REFERENCES decisions (decision_id),
    erased_at timestamptz NOT NULL DEFAULT now(),
    erased_by text NOT NULL,
    reason text NOT NULL
);

ALTER TABLE data_key_wraps ENABLE ROW LEVEL SECURITY;
ALTER TABLE erasures ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['data_key_wraps', 'erasures']
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

REVOKE ALL ON TABLE data_key_wraps, erasures FROM app_role, PUBLIC;
GRANT SELECT, INSERT ON TABLE data_key_wraps TO app_role;
GRANT SELECT ON TABLE erasures TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
