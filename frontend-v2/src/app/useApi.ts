import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "../api/queryKeys";

/**
 * 兼容现有页面返回形状的 TanStack Query adapter。
 *
 * 用法：
 *   const { data, loading, error, refetch } = useApi(
 *     (signal) => api.runs(undefined, 50),
 *     [],  // deps
 *   );
 *
 * fetcher 收到 signal，应传入 api 调用的 { signal } 选项以支持取消。
 */
export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: React.DependencyList = [],
): { data: T | null; loading: boolean; error: string; refetch: () => void } {
  const resource = fetcher.toString().replace(/\s+/g, " ");
  const query = useQuery({
    queryKey: queryKeys.legacy(resource, deps),
    queryFn: ({ signal }) => fetcher(signal),
  });
  return {
    data: query.data ?? null,
    loading: query.isLoading,
    error: query.error?.message ?? "",
    refetch: () => { void query.refetch(); },
  };
}
