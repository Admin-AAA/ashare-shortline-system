import type {
  HomeBootstrap,
  HomeInitialState,
  Preset,
  PrecisionAtKRow,
  PresetStatsRow,
  RecommendationCard,
  RecommendationDetail,
  RecommendationOutcome,
  DataSourceHealthRow,
  ModelRoute,
  LocalPoolStatus,
  PoolRefreshResult,
  RunDetail,
  RunFunnelAudit,
  RunResult,
  RunSummary,
  ScheduledTask,
  ScheduledTaskRun,
  ScheduledTaskPayload,
  SkillDetail,
  SkillMode,
  SkillStatsRow,
  SkillSummary,
  ScoreLineStatsRow,
  SetupStatsRow,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const start = performance.now();
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
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
      throw new Error(text || `Request failed: ${res.status}`);
    }
    return (await res.json()) as T;
  } catch (err) {
    const duration = Math.round(performance.now() - start);
    if ((err as Error).message === "UNAUTHORIZED") throw err;
    logger.error("api", `${method} ${path} failed`, { duration, error: String(err) });
    throw err;
  }
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    body: body === undefined ? undefined : JSON.stringify(body)
  };
}

async function requestWithTimeout<T>(path: string, timeoutMs: number, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "POST";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const start = performance.now();
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
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
      throw new Error(text || `Request failed: ${res.status}`);
    }
    return (await res.json()) as T;
  } catch (err) {
    const duration = Math.round(performance.now() - start);
    if ((err as Error).name === "AbortError") {
      logger.warn("api", `${method} ${path} 超时 (${timeoutMs}ms)`, { duration });
      throw new Error("请求超时，请检查网络后刷新重试");
    }
    if ((err as Error).message !== "UNAUTHORIZED") {
      logger.error("api", `${method} ${path} failed`, { duration, error: String(err) });
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
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

  presets: () => request<Preset[]>("/api/presets"),
  createPreset: (payload: PresetPayload) =>
    request<Preset>("/api/presets", json("POST", payload)),
  updatePreset: (presetId: string, payload: PresetPayload) =>
    request<Preset>(`/api/presets/${presetId}`, json("PATCH", payload)),

  runs: (runMode?: string) => request<RunSummary[]>(runMode ? `/api/runs?run_mode=${runMode}` : "/api/runs"),
  runDetail: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),
  runRecommendations: (runId: string) => request<RecommendationCard[]>(`/api/runs/${runId}/recommendations`),
  runFunnelAudit: (runId: string) => request<RunFunnelAudit>(`/api/runs/${runId}/funnel`),
  createRun: (payload: Record<string, unknown>) => requestWithTimeout<RunResult>("/api/runs", 300_000, json("POST", payload)),

  recommendationDetail: (id: string) => request<RecommendationDetail>(`/api/recommendations/${id}`),
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
  localPool: (params?: { u_limit?: number; u_offset?: number; b_limit?: number; b_offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.u_limit !== undefined) qs.set("u_limit", String(params.u_limit));
    if (params?.u_offset !== undefined) qs.set("u_offset", String(params.u_offset));
    if (params?.b_limit !== undefined) qs.set("b_limit", String(params.b_limit));
    if (params?.b_offset !== undefined) qs.set("b_offset", String(params.b_offset));
    const query = qs.toString();
    return request<LocalPoolStatus>(`/api/data-sources/pool${query ? `?${query}` : ""}`);
  },
  refreshLocalPool: () => requestWithTimeout<PoolRefreshResult>("/api/data-sources/pool/refresh", 300_000, json("POST")),
  setDataSourceEnabled: (sourceType: string, source: string, enabled: boolean) =>
    request<DataSourceHealthRow>(`/api/data-sources/${sourceType}/${source}/enabled`, json("POST", { enabled })),
  modelRoutes: () => request<ModelRoute[]>("/api/admin/model-routes"),
  createModelRoute: (payload: Record<string, unknown>) =>
    request<ModelRoute>("/api/admin/model-routes", json("POST", payload)),
  updateModelRoute: (routeId: string, payload: Record<string, unknown>) =>
    request<ModelRoute>(`/api/admin/model-routes/${routeId}`, json("PATCH", payload)),
  deleteModelRoute: (routeId: string) =>
    request<{ deleted: string }>(`/api/admin/model-routes/${routeId}`, json("DELETE")),
  reorderModelRoutes: (routeIds: string[]) =>
    request<ModelRoute[]>("/api/admin/model-routes/reorder", json("POST", { route_ids: routeIds })),

  scheduledTasks: () => request<ScheduledTask[]>("/api/admin/tasks"),
  scheduledTaskRuns: (limit = 100) => request<ScheduledTaskRun[]>(`/api/admin/task-runs?limit=${limit}`),
  createScheduledTask: (payload: ScheduledTaskPayload) =>
    request<ScheduledTask>("/api/admin/tasks", json("POST", payload)),
  updateScheduledTask: (taskId: string, payload: ScheduledTaskPayload) =>
    request<ScheduledTask>(`/api/admin/tasks/${taskId}`, json("PATCH", payload)),
  deleteScheduledTask: (taskId: string) =>
    request<{ deleted: string }>(`/api/admin/tasks/${taskId}`, json("DELETE")),
  runScheduledTask: (taskId: string) =>
    request<{ ok: boolean }>(`/api/admin/tasks/${taskId}/run`, json("POST")),

  skills: () => request<SkillSummary[]>("/api/skills"),
  installSkill: (payload: { source: string; skill_id?: string }) =>
    request<SkillDetail>("/api/skills/install", json("POST", payload)),
  skillDetail: (id: string) => request<SkillDetail>(`/api/skills/${id}`),
  setSkillMode: (id: string, mode: SkillMode) => request<{ skill_id: string; mode: SkillMode; enabled: boolean }>(`/api/skills/${id}/mode`, json("POST", { mode })),
  backtestSkill: (id: string) => request<{ skill_id: string; status: string; sample_count: number; hit_rate: number; avg_return: number; errors: string[] }>(`/api/skills/${id}/backtest`, json("POST")),

  resumeRun: (id: string) => requestWithTimeout<RunResult>(`/api/runs/${id}/resume`, 300_000, json("POST")),
  deleteRun: (id: string) => request<{ deleted: string }>(`/api/runs/${id}`, json("DELETE")),
  deleteRecommendation: (id: string) => request<{ deleted: string }>(`/api/recommendations/${id}`, json("DELETE")),
  deletePreset: (id: string) => request<{ deleted: string }>(`/api/presets/${id}`, json("DELETE")),

  statsOverview: (mode: "formal" | "experiment") => request<StatsOverview>(`/api/stats/overview?mode=${mode}`),
  statsBySkill: (mode: "formal" | "experiment") => request<SkillStatsRow[]>(`/api/stats/by-skill?mode=${mode}`),
  statsByPreset: (mode: "formal" | "experiment") => request<PresetStatsRow[]>(`/api/stats/by-preset?mode=${mode}`),
  metricsBySetup: (mode: "formal" | "experiment") => request<SetupStatsRow[]>(`/api/metrics/by-setup?mode=${mode}`),
  precisionAtK: (mode: "formal" | "experiment") => request<PrecisionAtKRow[]>(`/api/metrics/precision-at-k?mode=${mode}`),
  metricsByScoreLine: (mode: "formal" | "experiment") => request<ScoreLineStatsRow[]>(`/api/metrics/by-score-line?mode=${mode}`)
};
