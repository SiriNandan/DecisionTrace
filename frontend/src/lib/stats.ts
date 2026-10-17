import { DecisionSummaryItem } from "../types";

export interface Stats {
  total: number;
  counts: { APPROVE: number; DECLINE: number; REFER: number };
  approvalRate: number;
  changedByPerson: number;
  avgRisk: { APPROVE: number | null; DECLINE: number | null; REFER: number | null };
  months: Array<{ key: string; label: string; count: number }>;
}

export function computeStats(cases: DecisionSummaryItem[]): Stats {
  const counts = { APPROVE: 0, DECLINE: 0, REFER: 0 };
  const sums = { APPROVE: 0, DECLINE: 0, REFER: 0 };
  const byMonth = new Map<string, number>();
  let changed = 0;
  for (const d of cases) {
    counts[d.effective_outcome] += 1;
    sums[d.effective_outcome] += d.score;
    if (d.effective_outcome !== d.initial_outcome) changed += 1;
    const k = d.decided_at.slice(0, 7);
    byMonth.set(k, (byMonth.get(k) ?? 0) + 1);
  }
  const avg = (o: keyof typeof counts) => (counts[o] ? sums[o] / counts[o] : null);
  const months = [...byMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, count]) => ({
      key,
      count,
      label: new Date(`${key}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" }),
    }));
  return {
    total: cases.length,
    counts,
    approvalRate: cases.length ? counts.APPROVE / cases.length : 0,
    changedByPerson: changed,
    avgRisk: { APPROVE: avg("APPROVE"), DECLINE: avg("DECLINE"), REFER: avg("REFER") },
    months,
  };
}
