import type {
  Preset,
  RunSummary,
  ScheduledTask,
  ScheduledTaskPayload,
  ScheduledTaskType,
} from "../../types";
import { businessStatusLabel } from "../../utils/display";

export type TaskDraft = {
  task_id: string;
  name: string;
  task_type: ScheduledTaskType;
  cron_expression?: string;
  trigger_time: string;
  preset_id: string;
  enabled: boolean;
};

export type RouteDraft = {
  name: string;
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
  enabled: boolean;
  clear_api_key: boolean;
};

export type ManualRunDataSource = "local_pool" | "local_pool_preferred" | "realtime";
export const DEFAULT_RECOMMENDATION_DATA_SOURCE: ManualRunDataSource = "local_pool_preferred";

export type PresetDraft = {
  name: string;
  description: string;
  run_mode: Preset["run_mode"];
  market_scope: string;
  market_scope_value: string;
  risk_template: string;
  score_line: number;
  data_source: ManualRunDataSource;
  hide_recent_repeats: boolean;
};

export type PresetSkillDraft = {
  skill_id: string;
  weight: number;
  sort_order: number;
};

export type SectionLoadState = {
  presets: boolean;
  skills: boolean;
  tasks: boolean;
  taskRuns: boolean;
  modelRoutes: boolean;
  pool: boolean;
};

export const EMPTY_TASK_DRAFT: TaskDraft = {
  task_id: "",
  name: "",
  task_type: "recommendation_run",
  trigger_time: "09:05",
  preset_id: "",
  enabled: true,
};

export const EMPTY_ROUTE_DRAFT: RouteDraft = {
  name: "",
  provider: "openai_compatible",
  base_url: "",
  model: "",
  api_key: "",
  enabled: true,
  clear_api_key: false,
};

export const EMPTY_PRESET_DRAFT: PresetDraft = {
  name: "",
  description: "",
  run_mode: "formal",
  market_scope: "all",
  market_scope_value: "",
  risk_template: "balanced",
  score_line: 72,
  data_source: DEFAULT_RECOMMENDATION_DATA_SOURCE,
  hide_recent_repeats: true,
};

export const TERMINAL_RUN_STATUS = new Set(["completed", "partial_completed", "partial_failed", "failed", "interrupted", "cancelled"]);
export const LOCAL_POOL_GAP_SYNC_CODE_LIMIT = 0;
export const LOCAL_POOL_GAP_SYNC_DAYS = 90;
export const LOCAL_POOL_GAP_SYNC_MIN_BARS = 30;

export const INITIAL_SECTION_LOADING: SectionLoadState = {
  presets: true,
  skills: true,
  tasks: true,
  taskRuns: true,
  modelRoutes: true,
  pool: true,
};

export function makeTaskId(draft: TaskDraft) {
  const suffix = `${draft.task_type}_${draft.trigger_time || "time"}_${draft.preset_id || "pool"}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 52);
  return `task_${suffix}_${Date.now().toString(36)}`;
}

export function taskToPayload(task: ScheduledTask): ScheduledTaskPayload {
  return {
    task_id: task.task_id,
    name: task.name,
    task_type: task.task_type,
    cron_expression: task.cron_expression,
    trigger_time: task.trigger_time ?? cronToClockTime(task.cron_expression) ?? "09:05",
    payload: { ...(task.payload ?? {}) },
    enabled: task.enabled,
  };
}

export function buildTaskCron(draft: TaskDraft) {
  const time = draft.trigger_time || "09:05";
  const parts = draft.cron_expression?.split(/\s+/) ?? [];
  if (parts.length !== 5) return undefined;
  const [hourText, minuteText] = time.split(":", 2);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return draft.cron_expression;
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return draft.cron_expression;
  return [String(minute), String(hour), parts[2], parts[3], parts[4]].join(" ");
}

export function cronToClockTime(cronExpression: string) {
  const parts = cronExpression.split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour] = parts;
  const h = Number(hour);
  const m = Number(minute);
  if (!Number.isInteger(h) || !Number.isInteger(m)) return null;
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function buildPresetDraft(sourcePreset: Preset | null, presetCount: number): PresetDraft {
  if (!sourcePreset) {
    return {
      ...EMPTY_PRESET_DRAFT,
      name: `预设 ${presetCount + 1}`,
    };
  }
  return {
    name: sourcePreset.name,
    description: sourcePreset.description ?? "",
    run_mode: sourcePreset.run_mode,
    market_scope: sourcePreset.market_scope,
    market_scope_value: sourcePreset.market_scope_value ?? "",
    risk_template: sourcePreset.risk_template,
    score_line: sourcePreset.score_line,
    data_source: normalizeManualRunDataSource(sourcePreset.data_source),
    hide_recent_repeats: sourcePreset.hide_recent_repeats,
  };
}

export function buildPresetSkillDrafts(sourcePreset: Preset | null): PresetSkillDraft[] {
  if (!sourcePreset) return [];
  return normalizePresetSkillDrafts(sourcePreset.skills.map((skill, index) => ({
    skill_id: skill.skill_id,
    weight: skill.weight,
    sort_order: Number.isFinite(skill.sort_order) ? skill.sort_order : index + 1,
  })));
}

export function normalizePresetSkillDrafts(rows: PresetSkillDraft[]): PresetSkillDraft[] {
  return rows
    .filter((row) => Boolean(row.skill_id))
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((row, index) => ({
      ...row,
      weight: Number.isFinite(row.weight) && row.weight > 0 ? row.weight : 1,
      sort_order: index + 1,
    }));
}

export function normalizeManualRunDataSource(value: string | null | undefined): ManualRunDataSource {
  if (value === "local_pool_preferred" || value === "realtime") return value;
  return DEFAULT_RECOMMENDATION_DATA_SOURCE;
}

export function progressPercent(run: RunSummary) {
  if (typeof run.progress_pct === "number") {
    return Math.max(0, Math.min(100, run.progress_pct * 100));
  }
  if (!run.progress_total) return run.status === "completed" ? 100 : 0;
  return Math.max(0, Math.min(100, ((run.progress_current || 0) / run.progress_total) * 100));
}

export function labelText(value: string) {
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    all: "全市场",
    watchlist: "自选股",
    industry: "行业",
    conservative: "保守",
    balanced: "均衡",
    aggressive: "激进",
    local_pool: "仅本地池",
    local_pool_preferred: "本地池优先",
    realtime: "实时数据",
  };
  return labels[value] ?? businessStatusLabel(value);
}

export function describePresetScope(preset: Preset) {
  const scopeLabel = labelText(preset.market_scope);
  return preset.market_scope_value ? `${scopeLabel} · ${preset.market_scope_value}` : scopeLabel;
}

export function messageFromError(err: unknown) {
  if (err instanceof Error) return err.message === "UNAUTHORIZED" ? "请先登录" : err.message;
  return "操作失败";
}

export function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
