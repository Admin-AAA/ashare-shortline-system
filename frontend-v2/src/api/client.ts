import type {
  FilterEffectiveness,
  HomeBootstrap,
  HomeInitialState,
  MetricsOverview,
  MinutePrepResult,
  Preset,
  PrecisionAtKRow,
  PresetStatsRow,
  DataHealthDiagnostic,
  DataHealthRemediationResult,
  RecommendationCard,
  RecommendationDetail,
  RecommendationHistoryResponse,
  RecommendationOutcome,
  ReplayAnalysisResult,
  DataSourceHealthRow,
  ModelRoute,
  ModelRouteTestResult,
  LocalPoolGapSyncResult,
  LocalPoolStatus,
  PoolRefreshResult,
  RunDetail,
  RunFunnelAudit,
  RunRecordDetail,
  RunResult,
  RunSummary,
  MatrixRecordsResponse,
  MatrixRecordDetail,
  ScheduledTask,
  ScheduledTaskRun,
  ScheduledTaskPayload,
  SkillDetail,
  SkillMode,
  SkillStatsRow,
  SkillSummary,
  ScoreLineStatsRow,
  SetupStatsRow,
  StrategyListItem,
  StrategyStatsRow,
  StatsOverview
} from "../types";
import { createDashboardApi } from "./dashboard";
import { logger } from "../utils/logger";

type PresetPayload = Omit<Partial<Preset>, "skills"> & {
  skills?: Array<{ skill_id: string; weight: number; sort_order?: number }>;
};

export type AuthStatus = {
  enabled: boolean;
  authenticated: boolean;
};

const LONG_REQUEST_THRESHOLD_MS = 3000;
const CONTROL_CONFIG_TIMEOUT_MS = 30_000;

type RequestOptions = {
  timeoutMs?: number;
  signal?: AbortSignal;
};

const USER_ERROR_PATTERNS: Array<[RegExp, string]> = [
  [/lost connection to mysql server|pymysql\\.err\\.operationalerror|sqlalchemy\\.exc\\.operationalerror/i, "数据服务连接超时，请稍后重试；如果持续出现，请到控制中心查看数据池和服务状态。"],
  [/request failed:\\s*500|internal server error/i, "后端服务暂时不可用，请重试或到控制中心查看状态。"],
  [/failed to fetch|networkerror/i, "网络连接失败，请确认后端服务正在运行后重试。"],
];

let pageUnloading = false;
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => { pageUnloading = true; });
  window.addEventListener("beforeunload", () => { pageUnloading = true; });
}

async function request<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  const method = init?.method ?? "GET";
  const timeoutMs = options?.timeoutMs;
  const externalSignal = options?.signal;
  const controller = timeoutMs || externalSignal ? new AbortController() : null;
  const abortFromExternalSignal = () => controller?.abort();
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller?.abort();
    } else {
      externalSignal.addEventListener("abort", abortFromExternalSignal, { once: true });
    }
  }
  const timer = timeoutMs ? window.setTimeout(() => controller?.abort(), timeoutMs) : null;
  const start = performance.now();
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: controller?.signal,
      ...init
    });
    const duration = Math.round(performance.now() - start);
    logger.api(method, path, duration, res.status);
    if (duration > LONG_REQUEST_THRESHOLD_MS) {
      logger.warn("api", `慢请求: ${method} ${path}`, { duration, status: res.status });
    }
    if (!res.ok) {
      const text = await res.text();
      if (res.status === 401) {
        throw new Error("UNAUTHORIZED");
      }
      throw new Error(userFacingErrorMessage(text || `Request failed: ${res.status}`, res.status));
    }
    return (await res.json()) as T;
  } catch (err) {
    const duration = Math.round(performance.now() - start);
    if ((err as Error).name === "AbortError" || externalSignal?.aborted || isPageUnloadFetchAbort(err)) {
      if (externalSignal?.aborted || isPageUnloadFetchAbort(err)) {
        logger.info("api", `${method} ${path} 已取消`, { duration });
        throw abortError();
      }
      logger.warn("api", `${method} ${path} 超时 (${timeoutMs}ms)`, { duration });
      throw new Error("请求超时，请检查网络后刷新重试");
    }
    if ((err as Error).message === "UNAUTHORIZED") throw err;
    logger.error("api", `${method} ${path} failed`, { duration, error: String(err) });
    throw new Error(userFacingErrorMessage(err));
  } finally {
    if (timer !== null) {
      clearTimeout(timer);
    }
    externalSignal?.removeEventListener("abort", abortFromExternalSignal);
  }
}

function abortError() {
  const err = new Error("请求已取消");
  err.name = "AbortError";
  return err;
}

function isPageUnloadFetchAbort(err: unknown) {
  if (!pageUnloading) return false;
  const raw = err instanceof Error ? err.message : String(err ?? "");
  return /failed to fetch|networkerror|load failed/i.test(raw);
}

function userFacingErrorMessage(err: unknown, status?: number) {
  const raw = err instanceof Error ? err.message : String(err ?? "");
  for (const [pattern, message] of USER_ERROR_PATTERNS) {
    if (pattern.test(raw)) return message;
  }
  if (status === 500) return "后端服务暂时不可用，请重试或到控制中心查看状态。";
  if (status === 404) return "请求的数据不存在或接口尚未启用。";
  if (status === 403) return "当前没有权限执行这个操作。";
  return raw || "操作失败，请稍后重试。";
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    body: body === undefined ? undefined : JSON.stringify(body)
  };
}

async function requestWithTimeout<T>(path: string, timeoutMs: number, init?: RequestInit): Promise<T> {
  return request<T>(path, init, { timeoutMs });
}

/** 当前活跃的轮询 AbortController，用于取消 */
let activePollController: AbortController | null = null;

export function cancelActivePoll() {
  if (activePollController) {
    logger.info("poll", "取消活跃轮询");
    activePollController.abort();
    activePollController = null;
  }
}

export function createPollController(): AbortController {
  cancelActivePoll();
  activePollController = new AbortController();
  logger.debug("poll", "创建新轮询控制器");
  return activePollController;
}

export const api = {
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  login: (password: string) => request<{ ok: boolean }>("/api/auth/login", json("POST", { password })),
  dashboard: createDashboardApi(request),

  bootstrap: () => request<HomeBootstrap>("/api/home/bootstrap"),
  initial: () => request<HomeInitialState>("/api/home/initial"),

  presets: () => request<Preset[]>("/api/presets", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  createPreset: (payload: PresetPayload) =>
    request<Preset>("/api/presets", json("POST", payload)),
  updatePreset: (presetId: string, payload: PresetPayload) =>
    request<Preset>(`/api/presets/${presetId}`, json("PATCH", payload)),

  runs: (runMode?: string, limit?: number) => {
    const params = new URLSearchParams();
    if (runMode) params.set("run_mode", runMode);
    if (limit) params.set("limit", String(limit));
    const qs = params.toString();
    return request<RunSummary[]>(`/api/runs${qs ? `?${qs}` : ""}`);
  },
  runDetail: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),
  runRecommendations: (runId: string) => request<RecommendationCard[]>(`/api/runs/${runId}/recommendations`),
  runFunnelAudit: (runId: string) => request<RunFunnelAudit>(`/api/runs/${runId}/funnel`),
  createRun: (payload: Record<string, unknown>) => requestWithTimeout<RunResult>("/api/runs", 300_000, json("POST", payload)),
  startMatrixRun: (payload?: Record<string, unknown>) =>
    request<{ started: boolean; task_run: ScheduledTaskRun | null }>("/api/runs/matrix", json("POST", payload ?? {})),
  matrixRunStatus: () => request<{ task_run: ScheduledTaskRun | null }>("/api/runs/matrix/status"),

  recommendationDetail: (id: string) => request<RecommendationDetail>(`/api/recommendations/${id}`),
  recommendationHistory: (query?: {
    run_mode?: string;
    code?: string;
    label?: string;
    limit?: number;
    offset?: number;
  }) => {
    const params = new URLSearchParams();
    params.set("run_mode", query?.run_mode ?? "formal");
    if (query?.code) params.set("code", query.code);
    if (query?.label) params.set("label", query.label);
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    return request<RecommendationHistoryResponse>(`/api/recommendations/history?${params.toString()}`);
  },
  replayAnalysis: (runId: string) => request<ReplayAnalysisResult>(`/api/recommendations/runs/${runId}/replay-analysis`),
  watchRecommendation: (id: string, payload: { watch_status?: string; note?: string | null }) =>
    request<{ recommendation_id: string; watch_status: string; note?: string | null }>(`/api/recommendations/${id}/watch`, json("POST", payload)),
  ignoreRecommendation: (id: string, payload?: { note?: string | null }) =>
    request<{ recommendation_id: string; watch_status: string; note?: string | null }>(`/api/recommendations/${id}/ignore`, json("POST", payload ?? {})),
  reviewRecommendation: (id: string, payload: { user_label?: string; failure_reason?: string; review_text?: string }) =>
    request<{ recommendation_id: string; user_label?: string | null; failure_reason?: string | null; review_text?: string | null }>(`/api/recommendations/${id}/review`, json("POST", payload)),

  labelOutcome: (id: string, payload: { outcome_label: string; outcome_reason?: string; horizon_days?: number }) =>
    request<RecommendationOutcome>(`/api/outcomes/${id}/label`, json("POST", payload)),
  autoLabelOutcomes: (payload?: { horizon_days?: number; run_mode?: string }) => {
    const params = new URLSearchParams();
    if (payload?.horizon_days) params.set("horizon_days", String(payload.horizon_days));
    if (payload?.run_mode) params.set("run_mode", payload.run_mode);
    const qs = params.toString();
    return request<{ labeled: number }>(`/api/outcomes/label${qs ? `?${qs}` : ""}`, json("POST"));
  },

  dataSources: () => request<DataSourceHealthRow[]>("/api/data-sources"),
  localPool: (params?: { u_limit?: number; u_offset?: number; b_limit?: number; b_offset?: number; include_details?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.u_limit !== undefined) qs.set("u_limit", String(params.u_limit));
    if (params?.u_offset !== undefined) qs.set("u_offset", String(params.u_offset));
    if (params?.b_limit !== undefined) qs.set("b_limit", String(params.b_limit));
    if (params?.b_offset !== undefined) qs.set("b_offset", String(params.b_offset));
    if (params?.include_details !== undefined) qs.set("include_details", String(params.include_details));
    const query = qs.toString();
    return request<LocalPoolStatus>(`/api/data-sources/pool${query ? `?${query}` : ""}`, undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS });
  },
  refreshLocalPool: () => requestWithTimeout<PoolRefreshResult>("/api/data-sources/pool/refresh", 300_000, json("POST")),
  syncLocalPoolGaps: (payload?: { code_limit?: number; days?: number; min_bars?: number; dry_run?: boolean }) =>
    requestWithTimeout<LocalPoolGapSyncResult>("/api/data-sources/pool/sync-gaps", 300_000, json("POST", payload ?? {})),
  setDataSourceEnabled: (sourceType: string, source: string, enabled: boolean) =>
    request<DataSourceHealthRow>(`/api/data-sources/${sourceType}/${source}/enabled`, json("POST", { enabled })),
  modelRoutes: () => request<ModelRoute[]>("/api/admin/model-routes", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  createModelRoute: (payload: Record<string, unknown>) =>
    request<ModelRoute>("/api/admin/model-routes", json("POST", payload)),
  testModelRoute: (payload: Record<string, unknown>) =>
    request<ModelRouteTestResult>("/api/admin/model-routes/test", json("POST", payload), { timeoutMs: 35_000 }),
  updateModelRoute: (routeId: string, payload: Record<string, unknown>) =>
    request<ModelRoute>(`/api/admin/model-routes/${routeId}`, json("PATCH", payload)),
  deleteModelRoute: (routeId: string) =>
    request<{ deleted: string }>(`/api/admin/model-routes/${routeId}`, json("DELETE")),
  reorderModelRoutes: (routeIds: string[]) =>
    request<ModelRoute[]>("/api/admin/model-routes/reorder", json("POST", { route_ids: routeIds })),

  scheduledTasks: () => request<ScheduledTask[]>("/api/admin/tasks", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  scheduledTaskRuns: (limit = 100, taskId?: string) =>
    request<ScheduledTaskRun[]>(
      `/api/admin/task-runs?limit=${limit}${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ""}`,
      undefined,
      { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS },
    ),
  createScheduledTask: (payload: ScheduledTaskPayload) =>
    request<ScheduledTask>("/api/admin/tasks", json("POST", payload)),
  updateScheduledTask: (taskId: string, payload: ScheduledTaskPayload) =>
    request<ScheduledTask>(`/api/admin/tasks/${taskId}`, json("PATCH", payload)),
  deleteScheduledTask: (taskId: string) =>
    request<{ deleted: string }>(`/api/admin/tasks/${taskId}`, json("DELETE")),
  runScheduledTask: (taskId: string) =>
    request<{ ok: boolean }>(`/api/admin/tasks/${taskId}/run`, json("POST")),

  skills: () => request<SkillSummary[]>("/api/skills", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  strategies: (mode: "formal" | "experiment" = "formal") =>
    request<StrategyListItem[]>(`/api/strategies?mode=${mode}`, undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  installSkill: (payload: { source: string; skill_id?: string }) =>
    request<SkillDetail>("/api/skills/install", json("POST", payload)),
  skillDetail: (id: string) => request<SkillDetail>(`/api/skills/${id}`),
  setSkillMode: (id: string, mode: SkillMode) => request<{ skill_id: string; mode: SkillMode; enabled: boolean }>(`/api/skills/${id}/mode`, json("POST", { mode })),
  skillPerformanceSummary: (id: string) =>
    request<{
      skill_id: string;
      status: string;
      summary_type: string;
      source: string;
      runs_backtest: boolean;
      sample_count: number;
      hit_rate: number;
      avg_return: number;
      errors: string[];
    }>(`/api/skills/${id}/performance-summary`),
  batchSkillPerformanceSummaries: () =>
    request<Array<{
      skill_id: string;
      status: string;
      summary_type: string;
      source: string;
      runs_backtest: boolean;
      sample_count: number;
      hit_rate: number;
      avg_return: number;
      errors: string[];
    }>>(`/api/skills/performance-summaries`),

  resumeRun: (id: string) => requestWithTimeout<RunResult>(`/api/runs/${id}/resume`, 300_000, json("POST")),
  deleteRun: (id: string) => request<{ deleted: string }>(`/api/runs/${id}`, json("DELETE")),
  deleteRecommendation: (id: string) => request<{ deleted: string }>(`/api/recommendations/${id}`, json("DELETE")),
  deletePreset: (id: string) => request<{ deleted: string }>(`/api/presets/${id}`, json("DELETE")),

  metricsOverview: (mode: "formal" | "experiment", options?: RequestOptions) =>
    request<MetricsOverview>(`/api/metrics/overview?mode=${mode}`, undefined, {
      timeoutMs: options?.timeoutMs ?? 30_000,
      signal: options?.signal,
    }),
  filterEffectiveness: (mode: "formal" | "experiment", options?: RequestOptions) =>
    request<FilterEffectiveness>(`/api/metrics/filter-effectiveness?mode=${mode}`, undefined, {
      timeoutMs: options?.timeoutMs ?? 15_000,
      signal: options?.signal,
    }),
  dataHealth: (options?: RequestOptions) => request<DataHealthDiagnostic>("/api/admin/data-health", undefined, options),
  previewDataHealthRemediation: (payload?: {
    target_tables?: string[];
    dry_run?: boolean;
    capital_flow_code_limit?: number;
    daily_kbar_code_limit?: number;
    post_diagnosis?: boolean;
  }) =>
    request<DataHealthRemediationResult>("/api/admin/data-health/remediate", json("POST", payload ?? {}), { timeoutMs: 300_000 }),
  startDataHealthRemediation: (payload?: {
    target_tables?: string[];
    capital_flow_code_limit?: number;
    daily_kbar_code_limit?: number;
    minute_kbar_code_limit?: number;
    post_diagnosis?: boolean;
  }) =>
    request<{ started: boolean; task_run: ScheduledTaskRun | null }>("/api/admin/data-health/remediate", json("POST", payload ?? {})),
  dataHealthRemediationStatus: () =>
    request<{ task_run: ScheduledTaskRun | null }>("/api/admin/data-health/remediate/status"),
  temporalConsistency: (runMode?: string, options?: RequestOptions) =>
    request<Record<string, unknown>>(`/api/admin/temporal-consistency${runMode ? `?run_mode=${runMode}` : ""}`, undefined, options),
  previewMinuteGate: (payload: { start_date: string; end_date: string; strategy_id: string }) =>
    request<MinutePrepResult>("/api/runs/backtest", json("POST", { ...payload, action: "preview_minute_gate" }), { timeoutMs: 120_000 }),
  remediateMinuteGaps: (payload: { start_date: string; end_date: string; strategy_id: string }) =>
    request<MinutePrepResult>("/api/runs/backtest", json("POST", { ...payload, action: "remediate_minute_gaps" }), { timeoutMs: 120_000 }),
  statsOverview: (mode: "formal" | "experiment") => request<StatsOverview>(`/api/stats/overview?mode=${mode}`),
  statsBySkill: (mode: "formal" | "experiment") => request<SkillStatsRow[]>(`/api/stats/by-skill?mode=${mode}`),
  statsByPreset: (mode: "formal" | "experiment") => request<PresetStatsRow[]>(`/api/stats/by-preset?mode=${mode}`),
  metricsByStrategy: (mode: "formal" | "experiment") => request<StrategyStatsRow[]>(`/api/metrics/by-strategy?mode=${mode}`),
  metricsBySetup: (mode: "formal" | "experiment") => request<SetupStatsRow[]>(`/api/metrics/by-setup?mode=${mode}`),
  precisionAtK: (mode: "formal" | "experiment") => request<PrecisionAtKRow[]>(`/api/metrics/precision-at-k?mode=${mode}`),
  metricsByScoreLine: (mode: "formal" | "experiment") => request<ScoreLineStatsRow[]>(`/api/metrics/by-score-line?mode=${mode}`),

  // —— 记录中心：运行推荐记录 + 矩阵运行记录 ——
  runRecords: (params?: { run_mode?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.run_mode) qs.set("run_mode", params.run_mode);
    if (params?.limit) qs.set("limit", String(params.limit));
    const str = qs.toString();
    return request<RunSummary[]>(`/api/records/runs${str ? `?${str}` : ""}`);
  },
  runRecordDetail: (runId: string) => request<RunRecordDetail>(`/api/records/runs/${runId}`),
  matrixRecords: (params?: { page?: number; page_size?: number; code?: string }) => {
    const qs = new URLSearchParams();
    if (params?.page) qs.set("page", String(params.page));
    if (params?.page_size) qs.set("page_size", String(params.page_size));
    if (params?.code) qs.set("code", params.code);
    return request<MatrixRecordsResponse>(`/api/records/matrix?${qs.toString()}`);
  },
  matrixRecordDetail: (id: number) => request<MatrixRecordDetail>(`/api/records/matrix/${id}`)
};
