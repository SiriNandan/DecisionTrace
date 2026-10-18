-- SYNTHETIC DATA. A retail-bank style core lending schema used for the DecisionTrace import demo.
CREATE TABLE branches (branch_id serial PRIMARY KEY, branch_name text NOT NULL, region text NOT NULL);
CREATE TABLE customers (customer_id text PRIMARY KEY, full_name text NOT NULL, date_of_birth date NOT NULL, email text, mobile text,
  kyc_status text NOT NULL CHECK (kyc_status IN ('Verified','Pending','Failed')), marketing_consent boolean NOT NULL, home_branch_id int REFERENCES branches);
CREATE TABLE addresses (address_id serial PRIMARY KEY, customer_id text NOT NULL REFERENCES customers, line1 text, city text, postcode text, residency_status text, years_at_address int);
CREATE TABLE employment (employment_id serial PRIMARY KEY, customer_id text NOT NULL REFERENCES customers, employment_status text NOT NULL, employer text, months_in_role int);
CREATE TABLE accounts (account_id text PRIMARY KEY, customer_id text NOT NULL REFERENCES customers, account_type text NOT NULL, sort_code text, account_number text,
  opened_on date NOT NULL, overdraft_used boolean NOT NULL, avg_monthly_balance numeric(12,2));
CREATE TABLE loan_products (product_id serial PRIMARY KEY, product_name text NOT NULL, security_type text NOT NULL, max_term_months int NOT NULL, portfolio text NOT NULL);
CREATE TABLE applications (application_id text PRIMARY KEY, customer_id text NOT NULL REFERENCES customers, product_id int NOT NULL REFERENCES loan_products,
  branch_id int REFERENCES branches, channel text NOT NULL, purpose text, requested_amount numeric(12,2) NOT NULL, term_months int NOT NULL, deposit_amount numeric(12,2) NOT NULL DEFAULT 0,
  submitted_at timestamptz NOT NULL, underwriter text);
CREATE TABLE income_verifications (verification_id serial PRIMARY KEY, application_id text NOT NULL REFERENCES applications, verified_annual_income numeric(12,2), payslip_verified boolean NOT NULL, dti_ratio numeric(5,3));
CREATE TABLE collateral (collateral_id serial PRIMARY KEY, application_id text NOT NULL REFERENCES applications, collateral_type text NOT NULL, valuation numeric(12,2), ltv_ratio numeric(4,2));
CREATE TABLE bureau_reports (report_id serial PRIMARY KEY, application_id text NOT NULL REFERENCES applications, bureau text NOT NULL, credit_score int NOT NULL, missed_payments_24m int NOT NULL, credit_utilisation numeric(4,2), hard_enquiries_6m int NOT NULL, pulled_at timestamptz NOT NULL);
CREATE TABLE underwriting_decisions (decision_id serial PRIMARY KEY, application_id text NOT NULL UNIQUE REFERENCES applications, outcome text NOT NULL CHECK (outcome IN ('Approved','Declined','Referred')),
  decided_at timestamptz NOT NULL, probability_of_default numeric(5,3) NOT NULL, scorecard_version text NOT NULL, policy_version text NOT NULL);
CREATE TABLE decision_reason_codes (reason_code text PRIMARY KEY, description text NOT NULL);
CREATE TABLE decision_reasons (decision_id int NOT NULL REFERENCES underwriting_decisions, reason_code text NOT NULL REFERENCES decision_reason_codes, PRIMARY KEY (decision_id, reason_code));
CREATE TABLE repayments (repayment_id serial PRIMARY KEY, application_id text NOT NULL REFERENCES applications, due_on date NOT NULL, paid_on date, amount numeric(12,2) NOT NULL);
