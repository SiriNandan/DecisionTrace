/** Plain-English names for the customer-level patterns. */
export const SIGNAL_LABEL: Record<string, string> = {
  applicant_velocity: "Many applications in a short time",
  outcome_flip: "Approved soon after a decline",
  decline_streak_then_approval: "Approved after repeated declines",
  loan_stacking: "Several loans approved close together",
  amount_walk_down: "Amount lowered until approved",
  shared_details: "Same details under different customers",
};

export const signalLabel = (kind: string): string => SIGNAL_LABEL[kind] ?? kind.replace(/_/g, " ");
export const SEVERITY_TEXT = { high: "Look soon", warning: "Worth a look", info: "For information" } as const;
