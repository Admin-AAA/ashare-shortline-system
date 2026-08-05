import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../client";
import { queryKeys } from "../queryKeys";
import { dashboardRefreshInterval } from "../dashboardRefresh";

function useDocumentHidden() {
  const [hidden, setHidden] = useState(
    typeof document !== "undefined" ? document.hidden : false,
  );
  useEffect(() => {
    const update = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return hidden;
}

/**
 * One QueryClient key is the only dashboard cache. TanStack Query coalesces
 * concurrent consumers and aborts the consumed signal when the observer is
 * removed; this hook adds only a finite timer policy.
 */
export function useDashboardQuery() {
  const hidden = useDocumentHidden();
  const refreshCount = useRef(0);
  return useQuery({
    queryKey: queryKeys.dashboard(),
    queryFn: ({ signal }) => {
      refreshCount.current += 1;
      return api.dashboard.overview({ signal });
    },
    refetchInterval: (query) =>
      dashboardRefreshInterval({
        refreshCount: refreshCount.current,
        hidden,
        runStatus: query.state.data?.current_run.status,
        stale: query.state.data?.hardening?.stale,
      }),
    refetchIntervalInBackground: true,
  });
}
