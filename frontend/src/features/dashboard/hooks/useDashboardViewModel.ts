import { useCallback, useEffect, useState } from "react";
import { api } from "../../../api/client";
import type { DashboardOverview } from "../../../types";

export function useDashboardViewModel(refreshSignal = 0) {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const overview = await api.dashboard.overview({ signal });
      setData(overview);
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error ? err.message : "驾驶舱数据加载失败");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh, refreshSignal]);

  return { data, loading, error, refresh };
}

function isAbortError(err: unknown) {
  return err instanceof Error && err.name === "AbortError";
}
