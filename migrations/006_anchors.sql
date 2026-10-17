-- Sealing: per-lender batches of record fingerprints, hashed into a Merkle tree and signed (Idempotent)
-- Batches and leaves are never edited. Each batch names the root of the one before it.

CREATE TABLE IF NOT EXISTS anchor_batches (
    batch_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    seq int NOT NULL CHECK (seq >= 1),
    leaf_count int NOT NULL CHECK (leaf_count >= 1),
    root text NOT NULL,
    prev_root text NOT NULL,
    key_id text NOT NULL,
    signature text NOT NULL,
    signed_at timestamptz NOT NULL,
    signed_at_text text NOT NULL DEFAULT '',
    published boolean NOT NULL DEFAULT false,
    UNIQUE (tenant_id, seq)
);

-- The exact text that was signed, so checking never depends on how the database renders a time zone.
ALTER TABLE anchor_batches ADD COLUMN IF NOT EXISTS signed_at_text text NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS anchor_leaves (
    leaf_id bigserial PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES anchor_batches (batch_id),
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    leaf_index int NOT NULL CHECK (leaf_index >= 0),
    kind text NOT NULL CHECK (kind IN ('decision', 'corrections')),
    decision_id uuid NOT NULL REFERENCES decisions (decision_id),
    leaf_data jsonb NOT NULL,
    leaf_hash text NOT NULL,
    UNIQUE (batch_id, leaf_index)
);

-- A decision is sealed once; a second, overlapping seal run can't seal it again.
CREATE UNIQUE INDEX IF NOT EXISTS uq_anchor_decision_once ON anchor_leaves (decision_id) WHERE kind = 'decision';

CREATE INDEX IF NOT EXISTS idx_anchor_leaves_decision ON anchor_leaves (decision_id, kind, leaf_id DESC);

ALTER TABLE anchor_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE anchor_leaves ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['anchor_batches', 'anchor_leaves']
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

REVOKE ALL ON TABLE anchor_batches, anchor_leaves FROM app_role, PUBLIC;
GRANT SELECT, INSERT ON TABLE anchor_batches, anchor_leaves TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
