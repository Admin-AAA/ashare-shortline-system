import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import { queryKeys } from "../queryKeys";

const ACTIVE = new Set(["queued", "pending", "running"]);

export function useMatrixQuery() {
  return useQuery({
    queryKey: queryKeys.matrix(),
    queryFn: () => api.matrixRunStatus(),
    refetchInterval: (query) => {
      const status = query.state.data?.task_run?.status;
      return status && ACTIVE.has(status) ? 2_000 : false;
    },
  });
}

export function useStartMatrixMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.startMatrixRun({}),
    onSuccess: (data) => queryClient.setQueryData(queryKeys.matrix(), { task_run: data.task_run }),
  });
}
