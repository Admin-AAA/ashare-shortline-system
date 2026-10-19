import type { StandaloneMetricsResponse } from "../types";
import type {
  FilterEffectiveness,
  HomeBootstrap,
  MetricsOverview,
  Preset,
  PrecisionAtKRow,
  PresetStatsRow,
  DataHealthDiagnostic,
  RecommendationCard,
  RecommendationDetail,
  ExecutionConfirmation,
  ExecutionConfirmationResponse,
  RecommendationHistoryResponse,
  RecommendationOutcome,
  OutcomeSummary,
  WinRateSummary,
  LlmReviewHealth,
  LlmRouteHealthItem,
  TemporalConsistency,
  DDiagnostics,
  CapitalFlowFactorResponse,
  DNetBreakdown,
  ReplayAnalysisResult,
  DataSourceHealthRow,
  DataSourceProvider,
  DataSourceProvidersResponse,
  ModelRoute,
  ModelRouteTestResult,
  ProviderTestResult,
  RealtimePnlResponse,
  LocalPoolGapSyncResult,
  LocalPoolStatus,
  PoolRefreshResult,
  RunDetail,
  RunFunnelAudit,
  RunRecordDetail,
  RunResult,
  RunSummary,
  MatrixRecordsResponse,
  RuntimeMetricsSnapshot,
  MatrixRecordDetail,
  ScheduledTask,
  ScheduledTaskRun,
  ScheduledTaskPayload,
  SingleStockAnalysis,
  SkillDetail,
  SkillStatsRow,
  SkillSummary,
  ScoreLineStatsRow,
  SetupStatsRow,
  StrategyListItem,
  StrategyCenterDetail,
  StrategyStatsRow,
  StatsOverview,
  AlphaDecayResponse
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

export const UNAUTHORIZED_EVENT = "ashare:unauthorized";

export type MetricsSampleScope = "live_forward" | "historical_replay";

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

// 性能：并发的相同 GET（同路径、无外部取消信号）合并为一次网络请求；
// 后到的调用方拿到结果的深拷贝，避免共享对象被就地修改。响应内容不变。
const inflightGets = new Map<string, Promise<unknown>>();
function request<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  const method = init?.method ?? "GET";
  if (method !== "GET" || options?.signal) return requestNetwork<T>(path, init, options);
  const key = `${path}|${options?.timeoutMs ?? ""}`;
  const existing = inflightGets.get(key) as Promise<T> | undefined;
  if (existing) return existing.then((v) => structuredClone(v));
  const p = requestNetwork<T>(path, init, options).finally(() => inflightGets.delete(key));
  inflightGets.set(key, p);
  return p;
}

async function requestNetwork<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
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
        if (!path.startsWith("/api/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
        throw new Error("UNAUTHORIZED");
      }
      throw Object.assign(new Error(userFacingErrorMessage(text || `Request failed: ${res.status}`, res.status)), { status: res.status });
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
    if (typeof (err as { status?: number }).status === "number") throw err; // HTTP 错误已整理过文案，保留 status 供调用方判断
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

function metricsQuery(windowDays: number | undefined, sampleScope: MetricsSampleScope): string {
  const params = new URLSearchParams();
  if (windowDays !== undefined) params.set("window_days", String(windowDays));
  params.set("sample_scope", sampleScope);
  return params.toString();
}

export type HorizonMetrics = { n?: number; win?: number; avg?: number; pf?: number; days?: number; tot?: number; mdd?: number };
export type HorizonStrategy = {
  id: string; name: string; horizon: string; summary: string; schedule: string; hold_days: number; top_n: number;
  metrics: { recent_12m?: HorizonMetrics; phases?: Record<string, HorizonMetrics>; window?: string; note?: string };
};
export type HorizonStrategiesResponse = { horizon: string; strategies: HorizonStrategy[]; rejected?: { name: string; reason: string }[] };
export type HorizonPick = { code: string; name: string; total_mv: number; status: string; entry_date?: string; exit_date?: string; mark_date?: string; ret?: number; days?: number;
  rule_score?: number | null; rule_score_label?: string | null; rule_score_better?: "higher" | "lower" | null; selection_reason?: string | null; selection_rank?: number | null;
  cancel_code?: string | null; cancel_reason?: string | null; ret_includes_cost?: boolean | null;
  entry_price?: number | null; exit_price?: number | null; mark_price?: number | null; exit_delayed_days?: number | null };
export type HorizonBatch = { strategy_id: string; strategy_name: string; signal_date: string; status: string; avg_ret: number | null; picks: HorizonPick[] };
export type HorizonPicksResponse = { batches: HorizonBatch[]; total?: number; total_picks?: number; page?: number; page_size?: number; has_more?: boolean };
export type HorizonBar = { trade_date: string; open: number; high: number; low: number; close: number; pct_chg?: number; volume?: number; amount?: number };
export type HorizonPickDetail = { horizon: string; strategy_id: string; strategy_name: string; strategy_summary?: string; hold_days?: number; signal_date: string; code: string; name: string;
  batch_size?: number; batch_status?: string; pick: HorizonPick; bars: HorizonBar[]; other_signals?: { signal_date: string; strategy_id: string; strategy_name: string; horizon?: string }[] };
export type HorizonRunSkip = { skipped: string };
export type HorizonRunResponse = { horizon: string; as_of: string; force: boolean; result: Record<string, number | "no_data" | HorizonRunSkip>; files?: string[] };

export const api = {
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  login: (password: string) => request<{ ok: boolean }>("/api/auth/login", json("POST", { password })),
  dashboard: createDashboardApi(request),

  bootstrap: (options?: RequestOptions) => request<HomeBootstrap>("/api/home/bootstrap", undefined, options),

  presets: () => request<Preset[]>("/api/presets", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  createPreset: (payload: PresetPayload) =>
    request<Preset>("/api/presets", json("POST", payload)),
  updatePreset: (presetId: string, payload: PresetPayload) =>
    request<Preset>(`/api/presets/${presetId}`, json("PATCH", payload)),

  // fields=slim：后端去掉 result_summary（仍为数组），列表场景用
  runs: (limit?: number, fields?: "slim") => {
    const params = new URLSearchParams();
    if (limit) params.set("limit", String(limit));
    if (fields) params.set("fields", fields);
    const qs = params.toString();
    return request<RunSummary[]>(`/api/runs${qs ? `?${qs}` : ""}`);
  },
  runDetail: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),
  runRecommendations: (runId: string) => request<RecommendationCard[]>(`/api/runs/${runId}/recommendations`),
  runFunnelAudit: (runId: string) => request<RunFunnelAudit>(`/api/runs/${runId}/funnel`),
  createRun: (payload: Record<string, unknown>) => requestWithTimeout<RunResult>("/api/runs", 300_000, json("POST", payload)),

  recommendationDetail: (id: string) => request<RecommendationDetail>(`/api/recommendations/${id}`),
  recommendationRealtimePnl: (refresh = false) =>
    request<RealtimePnlResponse>(`/api/recommendations/realtime-pnl${refresh ? "?refresh=true" : ""}`),
  // 批量次日买入确认：对历史页当前页未结算推荐一次性实时确认，不调用 LLM/Agent
  confirmExecutionBatch: (recommendationIds: string[], refresh = false) =>
    request<ExecutionConfirmationResponse>(
      "/api/recommendations/execution-confirmations",
      json("POST", { recommendation_ids: recommendationIds, refresh }),
    ),
  confirmRecommendationExecution: (id: string) =>
    requestWithTimeout<ExecutionConfirmation>(
      `/api/recommendations/${id}/execution-confirmation`,
      300_000,
      json("POST", { include_agent: true }),
    ),
  recommendationHistory: (query?: {
    code?: string;
    label?: string;
    date_from?: string;
    date_to?: string;
    bucket?: string;
    setup_type?: string;
    strategy_id?: string;
    strategy_version_id?: string;
    limit?: number;
    offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (query?.code) params.set("code", query.code);
    if (query?.label) params.set("label", query.label);
    if (query?.date_from) params.set("date_from", query.date_from);
    if (query?.date_to) params.set("date_to", query.date_to);
    if (query?.bucket) params.set("bucket", query.bucket);
    if (query?.setup_type) params.set("setup_type", query.setup_type);
    if (query?.strategy_id) params.set("strategy_id", query.strategy_id);
    if (query?.strategy_version_id) params.set("strategy_version_id", query.strategy_version_id);
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    return request<RecommendationHistoryResponse>(`/api/recommendations/history?${params.toString()}`);
  },
  // 每只股票已结算推荐的真实历史表现（胜率/均收益），供实时候选卡「历史验证」使用
  recommendationOutcomeSummary: (codes: string[], options?: RequestOptions) => {
    if (codes.length === 0) return Promise.resolve<Record<string, OutcomeSummary>>({});
    const params = new URLSearchParams();
    codes.forEach((c) => params.append("codes", c));
    return request<Record<string, OutcomeSummary>>(`/api/recommendations/outcome-summary?${params.toString()}`, undefined, options);
  },
  replayAnalysis: (runId: string) => request<ReplayAnalysisResult>(`/api/recommendations/runs/${runId}/replay-analysis`),
  // 近 N 个已结算交易日 formal 推荐聚合胜率；零推荐/pending 日期不占窗口
  recommendationWinRate: (windowDays = 5, options?: RequestOptions) =>
    request<WinRateSummary>(`/api/recommendations/win-rate?window_days=${windowDays}`, undefined, options),
  // LLM 审查层健康度（驾驶舱状态条「AI 审查」徽章）—— 北极星 B 项可见性缺口修复
  llmReviewHealth: (options?: RequestOptions) =>
    request<LlmReviewHealth>("/api/llm/review-health", undefined, options),
  llmRouteHealth: () => request<LlmRouteHealthItem[]>("/api/llm/route-health"),
  watchRecommendation: (id: string, payload: { watch_status?: string; note?: string | null }) =>
    request<{ recommendation_id: string; watch_status: string; note?: string | null }>(`/api/recommendations/${id}/watch`, json("POST", payload)),
  ignoreRecommendation: (id: string, payload?: { note?: string | null }) =>
    request<{ recommendation_id: string; watch_status: string; note?: string | null }>(`/api/recommendations/${id}/ignore`, json("POST", payload ?? {})),
  reviewRecommendation: (id: string, payload: { user_label?: string; failure_reason?: string; review_text?: string }) =>
    request<{ recommendation_id: string; user_label?: string | null; failure_reason?: string | null; review_text?: string | null }>(`/api/recommendations/${id}/review`, json("POST", payload)),

  labelOutcome: (id: string, payload: { outcome_label: string; outcome_reason?: string; horizon_days?: number }) =>
    request<RecommendationOutcome>(`/api/outcomes/${id}/label`, json("POST", payload)),
  autoLabelOutcomes: (payload?: { horizon_days?: number }) => {
    const params = new URLSearchParams();
    if (payload?.horizon_days) params.set("horizon_days", String(payload.horizon_days));
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

  dataSourceProviders: () =>
    request<DataSourceProvidersResponse>("/api/admin/data-sources", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  setProviderEnabled: (providerName: string, enabled: boolean) =>
    request<DataSourceProvider>(`/api/admin/data-sources/${providerName}/enabled`, json("POST", { enabled })),
  testProvider: (providerName: string) =>
    request<ProviderTestResult>(`/api/admin/data-sources/${providerName}/test`, json("POST"), { timeoutMs: 35_000 }),
  reorderProviders: (providerNames: string[]) =>
    request<DataSourceProvidersResponse>(`/api/admin/data-sources/reorder`, json("POST", { provider_names: providerNames })),

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
  standaloneMetrics: () =>
    request<StandaloneMetricsResponse>("/api/strategies/standalone-metrics", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  horizonStrategies: (horizon: "mid" | "long") =>
    request<HorizonStrategiesResponse>(`/api/horizons/${horizon}/strategies`),
  horizonPicks: (horizon: "mid" | "long", strategyId?: string, limit = 30) =>
    request<HorizonPicksResponse>(`/api/horizons/${horizon}/picks?limit=${limit}${strategyId ? `&strategy_id=${encodeURIComponent(strategyId)}` : ""}`),
  horizonPicksPage: (horizon: "mid" | "long", page: number, pageSize: number, strategyId?: string) =>
    request<HorizonPicksResponse>(`/api/horizons/${horizon}/picks?page=${page}&page_size=${pageSize}${strategyId ? `&strategy_id=${encodeURIComponent(strategyId)}` : ""}`),
  horizonPickDetail: (horizon: "mid" | "long", strategyId: string, signalDate: string, code: string) =>
    request<HorizonPickDetail>(`/api/horizons/${horizon}/picks/${encodeURIComponent(strategyId)}/${signalDate}/${code}`),
  horizonRun: (horizon: "mid" | "long", payload: { as_of: string; strategy_ids?: string[]; force?: boolean }) =>
    requestWithTimeout<HorizonRunResponse>(`/api/horizons/${horizon}/run`, 900_000, json("POST", payload)),
  strategies: () =>
    request<StrategyListItem[]>("/api/strategies", undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS }),
  strategyDetail: (strategyId: string, versionId?: string) => {
    const params = new URLSearchParams();
    if (versionId) params.set("version_id", versionId);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<StrategyCenterDetail>(`/api/strategies/${encodeURIComponent(strategyId)}${suffix}`, undefined, { timeoutMs: CONTROL_CONFIG_TIMEOUT_MS });
  },
  installSkill: (payload: { source: string; skill_id?: string }) =>
    request<SkillDetail>("/api/skills/install", json("POST", payload)),
  skillDetail: (id: string) => request<SkillDetail>(`/api/skills/${id}`),
  setSkillEnabled: (id: string, enabled: boolean) => request<{ skill_id: string; enabled: boolean }>(`/api/skills/${id}/enabled`, json("POST", { enabled })),
  skillPerformanceSummary: (id: string) =>
    request<{
      skill_id: string;
      status: string;
      summary_type: string;
      source: string;
      runs_backtest: boolean;
      sample_count: number;
      triggered_count: number;
      success_count: number;
      hit_rate: number;
      avg_return: number;
      errors: string[];
    }>(`/api/skills/${id}/performance-summary`),
  analyzeStock: (payload: { code: string; trade_date?: string; include_llm?: boolean; skill_id?: string; preset_id?: string }) =>
    requestWithTimeout<SingleStockAnalysis>("/api/runs/analyze-stock", 300_000, json("POST", payload)),
  resumeRun: (id: string) => requestWithTimeout<RunResult>(`/api/runs/${id}/resume`, 300_000, json("POST")),
  deleteRun: (id: string) => request<{ deleted: string }>(`/api/runs/${id}`, json("DELETE")),
  deleteRecommendation: (id: string) => request<{ deleted: string }>(`/api/recommendations/${id}`, json("DELETE")),
  deletePreset: (id: string) => request<{ deleted: string }>(`/api/presets/${id}`, json("DELETE")),

  // 时间窗口：windowDays=N 取最近 N 天并截到统一结算截止日；不传则全部历史累计。
  // sampleScope 必须显式传给质量指标，避免 LIVE 与历史回放研究证据混算。
  metricsOverview: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward", options?: RequestOptions) => {
    const params = metricsQuery(windowDays, sampleScope);
    return request<MetricsOverview>(`/api/metrics/overview?${params}`, undefined, {
      timeoutMs: options?.timeoutMs ?? 30_000,
      signal: options?.signal,
    });
  },
  filterEffectiveness: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward", options?: RequestOptions) => {
    const params = metricsQuery(windowDays, sampleScope);
    return request<FilterEffectiveness>(`/api/metrics/filter-effectiveness?${params}`, undefined, {
      timeoutMs: options?.timeoutMs ?? 15_000,
      signal: options?.signal,
    });
  },
  dataHealth: (options?: RequestOptions & { forceRefresh?: boolean }) =>
    request<DataHealthDiagnostic>(
      `/api/admin/data-health${options?.forceRefresh ? "?force_refresh=true" : ""}`,
      undefined,
      options,
    ),
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
  temporalConsistency: (options?: RequestOptions) =>
    request<TemporalConsistency>("/api/admin/temporal-consistency", undefined, options),
  statsOverview: () => request<StatsOverview>(`/api/stats/overview`),
  statsBySkill: () => request<SkillStatsRow[]>(`/api/stats/by-skill`),
  statsByPreset: () => request<PresetStatsRow[]>(`/api/stats/by-preset`),
  statsAlphaDecay: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward") =>
    request<AlphaDecayResponse>(`/api/stats/alpha-decay?${metricsQuery(windowDays, sampleScope)}`),
  metricsByStrategy: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward") =>
    request<StrategyStatsRow[]>(`/api/metrics/by-strategy?${metricsQuery(windowDays, sampleScope)}`),
  metricsBySetup: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward") =>
    request<SetupStatsRow[]>(`/api/metrics/by-setup?${metricsQuery(windowDays, sampleScope)}`),
  precisionAtK: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward") =>
    request<PrecisionAtKRow[]>(`/api/metrics/precision-at-k?${metricsQuery(windowDays, sampleScope)}`),
  metricsByScoreLine: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward") =>
    request<ScoreLineStatsRow[]>(`/api/metrics/by-score-line?${metricsQuery(windowDays, sampleScope)}`),
  /** D 项诊断：真残留毒区 + 伪活跃反事实（与 self_check 同源，不改主口径） */
  dDiagnostics: (options?: RequestOptions) =>
    request<DDiagnostics>(`/api/metrics/d-diagnostics`, undefined, {
      timeoutMs: options?.timeoutMs ?? 30_000,
      signal: options?.signal,
    }),
  /** 资金流因子预测力分析（T1 新因子，待前瞻验证候选） */
  runtimeMetrics: () => request<RuntimeMetricsSnapshot>(`/api/metrics/runtime`),
  capitalFlowFactor: () => request<CapitalFlowFactorResponse>(`/api/metrics/capital-flow-factor`),
  /** D 项净成本 + 板块(20cm/10cm)分解（指标页透出，与 self_check 板块拆分同源口径） */
  dNetBreakdown: (windowDays?: number, sampleScope: MetricsSampleScope = "live_forward", options?: RequestOptions) =>
    request<DNetBreakdown>(`/api/metrics/d-net?${metricsQuery(windowDays, sampleScope)}`, undefined, {
      timeoutMs: options?.timeoutMs ?? 30_000,
      signal: options?.signal,
    }),

  // —— 记录中心：运行推荐记录 + 矩阵运行记录 ——
  runRecords: (params?: { limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    const str = qs.toString();
    return request<RunSummary[]>(`/api/records/runs${str ? `?${str}` : ""}`);
  },
  // 服务端分页：按 started_at 倒序，覆盖最近 500 次运行
  runRecordsPage: (params: { page: number; page_size: number; run_mode?: string; status?: string; preset_id?: string; trade_date_from?: string; trade_date_to?: string; fields?: "slim" | "full"; min_picks?: number; sort?: "started_at" | "picks" | "duration" | "status"; order?: "asc" | "desc" }) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
    return request<{ items: RunSummary[]; total: number; page: number; page_size: number }>(`/api/records/runs?${qs.toString()}`);
  },
  runRecordDetail: (runId: string) => request<RunRecordDetail>(`/api/records/runs/${runId}`),
  matrixRecords: (params?: { page?: number; page_size?: number; code?: string; analysis_type?: string; exclude_analysis_type?: string }) => {
    const qs = new URLSearchParams();
    if (params?.page) qs.set("page", String(params.page));
    if (params?.page_size) qs.set("page_size", String(params.page_size));
    if (params?.code) qs.set("code", params.code);
    if (params?.analysis_type) qs.set("analysis_type", params.analysis_type);
    if (params?.exclude_analysis_type) qs.set("exclude_analysis_type", params.exclude_analysis_type);
    return request<MatrixRecordsResponse>(`/api/records/matrix?${qs.toString()}`);
  },
  matrixRecordDetail: (id: number) => request<MatrixRecordDetail>(`/api/records/matrix/${id}`),
  /** 单股票评分历史（analysis_type=single_stock 隔离，按 code 倒序） */
  singleStockHistory: (code: string, page = 1, pageSize = 20) =>
    api.matrixRecords({ code, analysis_type: "single_stock", page, page_size: pageSize }),
};
