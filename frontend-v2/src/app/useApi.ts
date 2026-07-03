import { useEffect, useRef, useState } from "react";

/**
 * 通用数据获取 hook，合并 3+ 页面的重复 useEffect 模式：
 * AbortController + setLoading/setError + api.X().then.catch.finally
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
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshSignal, setRefreshSignal] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetcherRef
      .current(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          const name = (err as { name?: string })?.name;
          if (name !== "AbortError") {
            setError((err as { message?: string })?.message || "加载失败");
          }
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, refreshSignal]);

  const refetch = () => setRefreshSignal((n) => n + 1);
  return { data, loading, error, refetch };
}
