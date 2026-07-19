import type { RunSummary } from "../types";

export type PoolRunKind = "completed" | "empty" | "active" | "failed" | "unknown";

export type PoolRunDescription = {
  kind: PoolRunKind;
  label: string;
  countLabel: string;
};

type RunStateInput = Pick<RunSummary, "status" | "recommended_count">;

export function describePoolRun(run: RunStateInput): PoolRunDescription {
  if (run.status === "running") {
    return { kind: "active", label: "运行中", countLabel: "生成中" };
  }
  if (run.status === "queued") {
    return { kind: "active", label: "等待运行", countLabel: "等待中" };
  }
  if (run.status === "completed" && run.recommended_count === 0) {
    return { kind: "empty", label: "正常完成·无推荐", countLabel: "0 只" };
  }
  if (run.status === "completed") {
    return {
      kind: "completed",
      label: "已完成",
      countLabel: `${run.recommended_count} 只推荐`,
    };
  }
  if (["failed", "interrupted", "cancelled"].includes(run.status)) {
    const labels: Record<string, string> = {
      failed: "运行失败",
      interrupted: "运行中断",
      cancelled: "已取消",
    };
    return { kind: "failed", label: labels[run.status], countLabel: "—" };
  }
  return { kind: "unknown", label: run.status, countLabel: "—" };
}

export function summarizePoolRuns(runs: RunStateInput[]) {
  const summary = { total: runs.length, completed: 0, empty: 0, active: 0, failed: 0 };
  for (const run of runs) {
    const kind = describePoolRun(run).kind;
    if (kind === "completed") summary.completed += 1;
    if (kind === "empty") summary.empty += 1;
    if (kind === "active") summary.active += 1;
    if (kind === "failed") summary.failed += 1;
  }
  return summary;
}

/** Backend datetimes are stored as naive UTC and must be rendered in browser local time. */
export function formatPoolRunTime(iso?: string | null, tradeDate?: string) {
  if (!iso) return "--:--";
  const instant = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(instant.getTime())) return "--:--";

  const year = instant.getFullYear();
  const month = String(instant.getMonth() + 1).padStart(2, "0");
  const day = String(instant.getDate()).padStart(2, "0");
  const hour = String(instant.getHours()).padStart(2, "0");
  const minute = String(instant.getMinutes()).padStart(2, "0");
  const actualDate = `${year}-${month}-${day}`;
  return tradeDate && actualDate !== tradeDate ? `${month}-${day} ${hour}:${minute}` : `${hour}:${minute}`;
}
