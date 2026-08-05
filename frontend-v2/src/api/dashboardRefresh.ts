export const DASHBOARD_MAX_REFRESHES = 24;
export const DASHBOARD_VISIBLE_ACTIVE_MS = 5_000;
export const DASHBOARD_VISIBLE_IDLE_MS = 30_000;
export const DASHBOARD_HIDDEN_MS = 120_000;

const ACTIVE_RUN_STATUSES = new Set(["queued", "pending", "running"]);

export type DashboardRefreshFacts = {
  refreshCount: number;
  hidden: boolean;
  runStatus?: string | null;
  stale?: boolean;
};

/** Pure finite refresh policy; false means no further timer is scheduled. */
export function dashboardRefreshInterval(facts: DashboardRefreshFacts): number | false {
  if (facts.refreshCount >= DASHBOARD_MAX_REFRESHES) return false;
  if (facts.hidden) return DASHBOARD_HIDDEN_MS;
  if (facts.stale || ACTIVE_RUN_STATUSES.has(facts.runStatus ?? "")) {
    return DASHBOARD_VISIBLE_ACTIVE_MS;
  }
  return DASHBOARD_VISIBLE_IDLE_MS;
}
