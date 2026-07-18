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
