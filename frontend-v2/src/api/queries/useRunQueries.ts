import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import { queryKeys } from "../queryKeys";

const ACTIVE_RUN_STATUSES = new Set(["queued", "pending", "running"]);

export function usePresetsQuery() {
  return useQuery({ queryKey: queryKeys.presets(), queryFn: () => api.presets() });
}

export function useRunsQuery(limit?: number) {
  return useQuery({
    queryKey: queryKeys.runs(limit),
    queryFn: () => api.runs(limit),
    refetchInterval: (query) =>
      query.state.data?.some((run) => ACTIVE_RUN_STATUSES.has(run.status)) ? 300_000 : false,
  });
}

export function useRunQuery(runId: string | null, refreshSignal = 0) {
  return useQuery({
    queryKey: [...queryKeys.run(runId ?? ""), refreshSignal],
    queryFn: () => api.runDetail(runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) =>
      query.state.data && ACTIVE_RUN_STATUSES.has(query.state.data.status) ? 2_000 : false,
  });
}

export function useCreateRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.createRun(payload),
    onSuccess: (result) => {
      queryClient.setQueryData(queryKeys.run(result.run.run_id), result.run);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}
