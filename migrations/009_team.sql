-- Lender team management: a lender's own admin issues and withdraws keys, and can limit a key to some portfolios (Idempotent)
-- The API role may still only READ api_credentials. Issuing and withdrawing go through the two functions below, which
-- act only on the lender the session was opened for, so an admin can never touch another lender's keys.

ALTER TABLE api_credentials DROP CONSTRAINT IF EXISTS api_credentials_role_check;
ALTER TABLE api_credentials ADD CONSTRAINT api_credentials_role_check
    CHECK (role IN ('lender_ingest', 'lender_analyst', 'auditor', 'admin', 'lender_admin', 'lender_support'));

ALTER TABLE api_credentials ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE api_credentials ADD COLUMN IF NOT EXISTS portfolios text[];      -- NULL = every portfolio
ALTER TABLE api_credentials ADD COLUMN IF NOT EXISTS created_by bigint;
ALTER TABLE api_credentials ADD COLUMN IF NOT EXISTS revoked_by bigint;

-- An optional label for the group a decision belongs to (branch, product, region), set when the lender sends it.
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS portfolio text;
CREATE INDEX IF NOT EXISTS idx_decisions_portfolio ON decisions (tenant_id, portfolio);

CREATE OR REPLACE FUNCTION team_issue_key(
    p_hash text, p_role text, p_label text, p_expires timestamptz, p_portfolios text[], p_created_by bigint
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    t text := NULLIF(current_setting('app.tenant_id', true), '');
    new_id bigint;
BEGIN
    IF t IS NULL THEN
        RAISE EXCEPTION 'no lender selected';
    END IF;
    INSERT INTO api_credentials (tenant_id, key_sha256, role, label, expires_at, portfolios, created_by)
    VALUES (t, p_hash, p_role, p_label, p_expires, p_portfolios, p_created_by)
    RETURNING credential_id INTO new_id;
    RETURN new_id;
END
$$;

CREATE OR REPLACE FUNCTION team_revoke_key(p_id bigint, p_by bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    t text := NULLIF(current_setting('app.tenant_id', true), '');
    n int;
BEGIN
    IF t IS NULL THEN
        RAISE EXCEPTION 'no lender selected';
    END IF;
    UPDATE api_credentials SET revoked_at = now(), revoked_by = p_by
    WHERE credential_id = p_id AND tenant_id = t AND revoked_at IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n = 1;
END
$$;

REVOKE ALL ON FUNCTION team_issue_key(text, text, text, timestamptz, text[], bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION team_revoke_key(bigint, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION team_issue_key(text, text, text, timestamptz, text[], bigint) TO app_role;
GRANT EXECUTE ON FUNCTION team_revoke_key(bigint, bigint) TO app_role;
