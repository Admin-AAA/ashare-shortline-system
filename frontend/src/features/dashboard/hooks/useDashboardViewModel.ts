import { useCallback, useEffect, useState } from "react";
import { api } from "../../../api/client";
import type { DashboardOverview } from "../../../types";

export function useDashboardViewModel() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const overview = await api.dashboard.overview();
      setData(overview);
    } catch (err) {
      setError(err instanceof Error ? err.message : "驾驶舱数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
