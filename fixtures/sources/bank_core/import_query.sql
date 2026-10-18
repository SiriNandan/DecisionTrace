SELECT a.application_id AS "Loan Number", c.customer_id AS "Customer ID", c.full_name AS "Customer Name", c.email AS "Email",
  b.branch_name AS "Branch", p.product_name AS "Product", p.security_type AS "Security Type", p.portfolio AS "Portfolio",
  a.channel AS "Application Channel", a.purpose AS "Loan Purpose", a.underwriter AS "Underwriter",
  e.employment_status AS "Employment Status", e.months_in_role AS "Months in Role", ad.residency_status AS "Residency Status",
  a.requested_amount AS "Loan Amount Requested", a.term_months AS "Term (months)", a.deposit_amount AS "Deposit",
  iv.verified_annual_income AS "Verified Annual Income", iv.dti_ratio AS "Debt-to-Income", iv.payslip_verified AS "Payslip Verified",
  col.ltv_ratio AS "LTV", br.credit_score AS "Credit Score", br.missed_payments_24m AS "Missed Payments 24m",
  br.credit_utilisation AS "Credit Utilisation", br.hard_enquiries_6m AS "Hard Enquiries 6m",
  ac.overdraft_used AS "Overdraft Used", ac.avg_monthly_balance AS "Average Balance", c.kyc_status AS "KYC Verification", c.marketing_consent AS "Marketing Consent",
  d.outcome AS "Decision", d.decided_at AS "Decision Date", d.probability_of_default AS "PD",
  (SELECT string_agg(dr.reason_code, ', ' ORDER BY dr.reason_code) FROM decision_reasons dr WHERE dr.decision_id = d.decision_id) AS "Reason Codes",
  d.scorecard_version AS "Scorecard", d.policy_version AS "Policy Version"
FROM applications a
JOIN customers c ON c.customer_id = a.customer_id
JOIN loan_products p ON p.product_id = a.product_id
LEFT JOIN branches b ON b.branch_id = a.branch_id
LEFT JOIN employment e ON e.customer_id = c.customer_id
LEFT JOIN addresses ad ON ad.customer_id = c.customer_id
LEFT JOIN income_verifications iv ON iv.application_id = a.application_id
LEFT JOIN collateral col ON col.application_id = a.application_id
LEFT JOIN bureau_reports br ON br.application_id = a.application_id
LEFT JOIN LATERAL (SELECT * FROM accounts x WHERE x.customer_id = c.customer_id ORDER BY opened_on LIMIT 1) ac ON true
JOIN underwriting_decisions d ON d.application_id = a.application_id
ORDER BY a.application_id
