-- Customer choices: an append-only ledger, and each lender's policy on AI-written summaries (Idempotent)
-- A choice is never edited. Changing your mind adds a new row; the newest row for a decision and purpose is what counts.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ai_summary_policy text NOT NULL DEFAULT 'ask'
    CHECK (ai_summary_policy IN ('ask', 'allow', 'never'));

CREATE TABLE IF NOT EXISTS consents (
    consent_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants (tenant_id)
        DEFAULT COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), 'demo'),
    decision_id uuid NOT NULL REFERENCES decisions (decision_id),
    purpose text NOT NULL CHECK (purpose IN ('ai_summary')),
    granted boolean NOT NULL,
    wording_version text NOT NULL,
    given_by text NOT NULL CHECK (given_by IN ('customer', 'staff_for_customer')),
    claim_id bigint,
    recorded_by_role text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_consents_decision ON consents (decision_id, purpose, consent_id DESC);

ALTER TABLE consents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON consents;
CREATE POLICY tenant_isolation ON consents USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

REVOKE ALL ON TABLE consents FROM app_role, PUBLIC;
GRANT SELECT, INSERT ON TABLE consents TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
