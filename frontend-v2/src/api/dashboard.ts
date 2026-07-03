import type { DashboardOverview } from "../types";

type DashboardRequestOptions = {
  timeoutMs?: number;
  signal?: AbortSignal;
};

export type DashboardApi = {
  overview: (options?: DashboardRequestOptions) => Promise<DashboardOverview>;
};

export function createDashboardApi(
  request: <T>(path: string, init?: RequestInit, options?: DashboardRequestOptions) => Promise<T>,
): DashboardApi {
  return {
    overview: (options) =>
      request<DashboardOverview>("/api/dashboard/overview", undefined, {
        timeoutMs: options?.timeoutMs ?? 30_000,
        signal: options?.signal,
      }),
  };
}
