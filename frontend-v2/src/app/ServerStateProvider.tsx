import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 10 * 60_000,
      // 超时/401 不重试：后端繁忙时重试只会把负载翻倍；网络瞬断仍重试 1 次
      retry: (failureCount, error) => failureCount < 1 && !/超时|UNAUTHORIZED/.test((error as Error)?.message ?? ""),
      refetchOnWindowFocus: false,
    },
    mutations: { retry: false },
  },
});

export function ServerStateProvider({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
