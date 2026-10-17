-- Data-quality findings and corrections (Idempotent)
-- Nothing here edits an original record. A correction is a new hash-linked row that points at the original item.

CREATE TABLE IF NOT EXISTS quality_findings (
    finding_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    decision_id uuid NOT NULL REFERENCES decisions (decision_id),
    rule_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('missing', 'implausible', 'conflict', 'inconsistent', 'outlier')),
    severity text NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    evidence_ref text NOT NULL DEFAULT '',
    field text NOT NULL DEFAULT '',
    observed jsonb NOT NULL DEFAULT '{}'::jsonb,
    message text NOT NULL,
    suggested_fix jsonb,
    ai_note text,
    found_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (decision_id, rule_id, evidence_ref, field)
);

CREATE TABLE IF NOT EXISTS finding_reviews (
    review_id bigserial PRIMARY KEY,
    finding_id bigint NOT NULL REFERENCES quality_findings (finding_id),
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    action text NOT NULL CHECK (action IN ('corrected', 'dismissed')),
    note text NOT NULL,
    reviewer_credential bigint,
    reviewer_role text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now()
);

-- One hash-linked chain of corrections per decision, kept apart from the decision's own chain.
CREATE TABLE IF NOT EXISTS corrections (
    correction_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    decision_id uuid NOT NULL REFERENCES decisions (decision_id),
    seq int NOT NULL CHECK (seq >= 1),
    finding_id bigint REFERENCES quality_findings (finding_id),
    evidence_ref text NOT NULL,
    field text NOT NULL,
    original_entry_hash text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    prev_hash text NOT NULL,
    entry_hash text NOT NULL,
    corrected_by_role text NOT NULL,
    corrected_by_credential bigint,
    created_at timestamptz NOT NULL,
    UNIQUE (decision_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_findings_tenant ON quality_findings (tenant_id, finding_id DESC);

ALTER TABLE quality_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE finding_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE corrections ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['quality_findings', 'finding_reviews', 'corrections']
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

REVOKE ALL ON TABLE quality_findings, finding_reviews, corrections FROM app_role, PUBLIC;
GRANT SELECT, INSERT ON TABLE quality_findings, finding_reviews, corrections TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
