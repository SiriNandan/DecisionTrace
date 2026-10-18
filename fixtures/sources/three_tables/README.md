# Three-table sample (synthetic, fictional)

Made-up data in the shape a bank's separate exports often take. No real people, no real bank.
Do not read anything into the numbers.

- `applicants.csv`: one row per applicant (Cust No is the shared key).
- `loans.csv`: one row per loan decision; Status is a code (A approved, D declined, R referred).
- `decline_reasons.csv`: zero, one or several rows per declined loan.

Deliberate mess: different column names to our envelope; status codes; C1004's income (2,850) looks like a
monthly figure in a yearly column; C1009 is duplicated in applicants.csv and LN-2002 in loans.csv; LN-2010 has
no applicant (C9999); LN-2009 is a decline with no reason; there is no risk score, model version or
policy version anywhere, as in many real exports.
