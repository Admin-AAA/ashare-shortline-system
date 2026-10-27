import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../client";
import { queryKeys } from "../queryKeys";

const ACTIVE = new Set(["queued", "pending", "running"]);

export function useDataHealthQuery() {
  return useQuery({ queryKey: queryKeys.dataHealth(), queryFn: () => api.dataHealth() });
}

export function useDataQualityQuery() {
  return useQuery({ queryKey: queryKeys.dataQuality(), queryFn: () => api.dataQuality(false), refetchInterval: 5 * 60_000 });
}

export function useDataHealthRemediationQuery() {
  return useQuery({
    queryKey: queryKeys.dataHealthRemediation(),
    queryFn: () => api.dataHealthRemediationStatus(),
    refetchInterval: (query) => {
      const status = query.state.data?.task_run?.status;
      return status && ACTIVE.has(status) ? 3_000 : false;
    },
  });
}

export function useStartDataHealthRemediationMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.startDataHealthRemediation({ post_diagnosis: true }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.dataHealthRemediation(), { task_run: data.task_run });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dataHealth() });
    },
  });
}
