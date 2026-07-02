import type { RunSummary } from "../types";
import { formatApiDateTime } from "../utils/display";

export function displayText(key: string): string {
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    data_sync: "数据同步",
    pool_refresh: "行情池刷新",
    data_backfill: "数据回填",
    recommendation_run: "自动推荐",
    local_pool: "仅本地池",
    local_pool_preferred: "本地池优先",
    realtime: "仅实时数据源",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "运行失败",
    interrupted: "已中断",
    manual: "手动触发",
    schedule: "定时触发",
    api: "接口写入",
    stale: "疑似中断",
    prepare: "准备运行",
    starting: "启动中",
    market_data: "市场数据",
    scope_filter: "范围过滤",
    auxiliary_data: "辅助数据",
    feature_build: "特征构建",
    rule_scan: "规则扫描",
    skill_gate: "规则门",
    skill_review: "Skill 深审",
    aggregate: "聚合",
    persist: "保存结果",
  };
  return labels[key] ?? key;
}

export function displayTagText(text: string) {
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    data_sync: "数据同步",
    pool_refresh: "行情池刷新",
    data_backfill: "数据回填",
    recommendation_run: "自动推荐",
    manual: "手动触发",
    schedule: "定时触发",
    api: "接口写入",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "运行失败",
    interrupted: "已中断",
    stale: "疑似中断",
  };
  return displayText(text) || labels[text] || text;
}

export function toneForTag(text: string) {
  if (["formal", "completed"].includes(text)) return "positive";
  if (["experiment", "queued", "running", "manual"].includes(text)) return "warning";
  if (["failed", "interrupted", "stale"].includes(text)) return "danger";
  return "neutral";
}

export function progressPercent(run: RunSummary) {
  if (typeof run.progress_pct === "number") {
    return Math.max(0, Math.min(100, run.progress_pct * 100));
  }
  if (!run.progress_total) return 0;
  return Math.max(0, Math.min(100, ((run.progress_current || 0) / run.progress_total) * 100));
}

export function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时`;
}

export function formatDateTime(value?: string | null) {
  return formatApiDateTime(value, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
