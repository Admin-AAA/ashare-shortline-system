export type DataSourceHealthStatus = "healthy" | "degraded" | "disabled" | "untested" | string;

export interface DataSourceHealthSummaryRow {
  status: DataSourceHealthStatus;
  success_rate?: number | null;
}

/**
 * Count sources whose latest health event says they are degraded.
 *
 * success_rate is cumulative history, so it must not override the current
 * status: a source can have recovered while its historical failure ratio stays
 * low until the old samples age out.
 */
export function countDegradedDataSources(rows: readonly DataSourceHealthSummaryRow[]): number {
  return rows.filter((row) => row.status === "degraded").length;
}

/** Count recovered sources whose cumulative success rate is still below 90%. */
export function countHistoricalLowSuccessSources(rows: readonly DataSourceHealthSummaryRow[]): number {
  return rows.filter(
    (row) => row.status === "healthy" && row.success_rate != null && row.success_rate < 0.9,
  ).length;
}
