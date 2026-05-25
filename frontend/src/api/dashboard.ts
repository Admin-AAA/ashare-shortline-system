import type { DashboardOverview } from "../types";

export type DashboardApi = {
  overview: () => Promise<DashboardOverview>;
};

export function createDashboardApi(request: <T>(path: string, init?: RequestInit) => Promise<T>): DashboardApi {
  return {
    overview: () => request<DashboardOverview>("/api/dashboard/overview"),
  };
}
