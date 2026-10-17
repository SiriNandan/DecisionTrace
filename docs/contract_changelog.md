# DecisionTrace Contract Changelog

All changes to `backend/contract.py` (`ReconstructionReport`, `RuleCheck`, `Reason`, `Claim`, `Override`) require a dated entry in this file.

## [1.0.0] - 2026-10-17

### Added
- Defined Pydantic v2 `ReconstructionReport` contract in `backend/contract.py` to represent reconstructed decision explanations that are tamper-evident against a retained reference and where the preserved record supports audit and explanation:
  - `ReconstructionReport`: `decision_id`, `outcome`, `score`, `chain_verified`, `replay_verified`, `replay_tolerance`, `completeness` (`0.0`–`1.0`), `missing_evidence` (`list[str]`), `principal_reasons` (`list[str]`), `rule_checks` (`list[RuleCheck]`), `reasons` (`list[Reason]`), `policy_note`, `human_override` (`Override | None`), `guardrail_rejections` (`int = 0`), `status` (`ok | timeout | error | evidence_unavailable`).
  - `RuleCheck`: `rule_id`, `observed_value`, `operator`, `threshold`, `passed`, `section`, `evidence_ref`.
  - `Reason`: `claims` (`list[Claim]`).
  - `Claim`: `text`, `citation` (`evidence_ref` or policy section id), `field` (name in the cited item), `value` (the asserted value).
  - `Override`: `new_outcome`, `reason` (minimum 20 characters), `actor_role`, `created_at`.

