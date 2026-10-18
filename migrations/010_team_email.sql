-- A team member's email, so the invite (with their key) can be sent to them and their profile can show it (Idempotent)
ALTER TABLE api_credentials ADD COLUMN IF NOT EXISTS email text;
-- A person may hold several roles: `role` is the main one (named in the access log), `extra_roles` the others; they get the union of what each allows.
ALTER TABLE api_credentials ADD COLUMN IF NOT EXISTS extra_roles text[];

DROP FUNCTION IF EXISTS team_issue_key(text, text, text, timestamptz, text[], bigint);
DROP FUNCTION IF EXISTS team_issue_key(text, text, text, timestamptz, text[], bigint, text);

CREATE OR REPLACE FUNCTION team_issue_key(
    p_hash text, p_role text, p_label text, p_expires timestamptz, p_portfolios text[], p_created_by bigint, p_email text, p_extra_roles text[]
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    t text := NULLIF(current_setting('app.tenant_id', true), '');
    new_id bigint;
BEGIN
    IF t IS NULL THEN
        RAISE EXCEPTION 'no lender selected';
    END IF;
    INSERT INTO api_credentials (tenant_id, key_sha256, role, label, expires_at, portfolios, created_by, email, extra_roles)
    VALUES (t, p_hash, p_role, p_label, p_expires, p_portfolios, p_created_by, p_email, p_extra_roles)
    RETURNING credential_id INTO new_id;
    RETURN new_id;
END
$$;

REVOKE ALL ON FUNCTION team_issue_key(text, text, text, timestamptz, text[], bigint, text, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION team_issue_key(text, text, text, timestamptz, text[], bigint, text, text[]) TO app_role;
