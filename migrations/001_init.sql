-- DecisionTrace Initial Schema Migration (Idempotent)

CREATE TABLE IF NOT EXISTS decisions (
    decision_id uuid PRIMARY KEY,
    application_id text,
    decided_at timestamptz,
    outcome text CHECK (outcome IN ('APPROVE', 'DECLINE', 'REFER')),
    model_version text,
    policy_version text,
    score numeric(8,6),
    chain_length int,
    chain_head text
);

CREATE TABLE IF NOT EXISTS decision_snapshots (
    snapshot_id bigserial PRIMARY KEY,
    decision_id uuid REFERENCES decisions(decision_id),
    seq int,
    evidence_type text,
    evidence_ref text,
    source_id text,
    source_timestamp timestamptz,
    payload jsonb,
    payload_sha256 text,
    prev_hash text,
    entry_hash text,
    captured_at timestamptz,
    UNIQUE (decision_id, seq)
);

CREATE TABLE IF NOT EXISTS overrides (
    override_id bigserial PRIMARY KEY,
    decision_id uuid REFERENCES decisions(decision_id),
    new_outcome text,
    reason text CHECK (length(reason) >= 20),
    actor_role text,
    created_at timestamptz
);

CREATE TABLE IF NOT EXISTS policies (
    policy_id text PRIMARY KEY,
    policy_family text,
    effective_from date,
    effective_to date,
    text_sha256 text,
    body text
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_version text PRIMARY KEY,
    artifact_sha256 text,
    preprocessing_version text,
    feature_order jsonb,
    intercept numeric,
    coefficients jsonb,
    feature_means jsonb,
    feature_stds jsonb,
    created_at timestamptz
);

CREATE TABLE IF NOT EXISTS evidence_status (
    decision_id uuid,
    evidence_ref text,
    status text CHECK (status IN ('available', 'erased_by_retention', 'missing')),
    changed_at timestamptz
);

-- Create app_role idempotently
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role') THEN
        CREATE ROLE app_role WITH LOGIN PASSWORD 'app_role_local_dev';
    ELSE
        ALTER ROLE app_role WITH LOGIN PASSWORD 'app_role_local_dev';
    END IF;
END
$$;

GRANT app_role TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO app_role;

-- Reset and apply least-privilege grants for app_role
REVOKE ALL ON TABLE
    decisions,
    decision_snapshots,
    overrides,
    policies,
    model_registry,
    evidence_status
FROM app_role, PUBLIC;

GRANT SELECT, INSERT ON TABLE
    decisions,
    decision_snapshots,
    overrides,
    evidence_status
TO app_role;

GRANT SELECT ON TABLE
    policies,
    model_registry
TO app_role;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;

