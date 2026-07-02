import { memo, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, RefreshCcw, Search } from "lucide-react";
import { api } from "../api/client";
import type { RunFunnelAudit, RunSummary } from "../types";
import { ALL_STAGES, ALL_STATUS, stageLabel, statusLabel } from "./runFunnelAuditHelpers";
import {
  AuditDistributions,
  AuditRowDetail,
  AuditStageSummary,
  AuditTable,
  EmptyAudit,
} from "./RunFunnelAuditComponents";

type Props = {
  runs: RunSummary[];
};

export default memo(function RunFunnelAuditPanel({ runs }: Props) {
  const completedRuns = useMemo(() => runs.filter((run) => run.status === "completed"), [runs]);
  const [runId, setRunId] = useState("");
  const [stage, setStage] = useState(ALL_STAGES);
  const [status, setStatus] = useState(ALL_STATUS);
  const [query, setQuery] = useState("");
  const [selectedCode, setSelectedCode] = useState("");
  const [audit, setAudit] = useState<RunFunnelAudit | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!runId && completedRuns[0]) {
      setRunId(completedRuns[0].run_id);
    }
  }, [completedRuns, runId]);

  useEffect(() => {
    if (!runId) return;
    void loadAudit(runId);
  }, [runId]);

  async function loadAudit(targetRunId = runId) {
    if (!targetRunId) return;
    setLoading(true);
    setError("");
    try {
      const data = await api.runFunnelAudit(targetRunId);
      setAudit(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  const stageOptions = useMemo(() => {
    const values = new Set<string>();
    audit?.rows.forEach((row) => values.add(row.final_stage));
    return [ALL_STAGES, ...Array.from(values)];
  }, [audit]);

  const rows = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (audit?.rows ?? []).filter((row) => {
      if (stage !== ALL_STAGES && row.final_stage !== stage) return false;
      if (status !== ALL_STATUS && row.final_status !== status) return false;
      if (!normalized) return true;
      return [row.code, row.name, row.industry, row.final_reason, row.trace_summary]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalized));
    });
  }, [audit, query, stage, status]);

  const statusOptions = useMemo(() => {
    const values = new Set<string>();
    audit?.rows.forEach((row) => values.add(row.final_status));
    return [ALL_STATUS, ...Array.from(values)];
  }, [audit]);

  const selectedRow = useMemo(
    () => rows.find((row) => row.code === selectedCode) ?? rows[0] ?? null,
    [rows, selectedCode],
  );

  useEffect(() => {
    if (!rows.length) {
      if (selectedCode) setSelectedCode("");
      return;
    }
    if (!selectedCode || !rows.some((row) => row.code === selectedCode)) {
      setSelectedCode(rows[0].code);
    }
  }, [rows, selectedCode]);

  return (
    <div className="audit-workspace">
      <section className="panel audit-control-panel">
        <div className="panel-title">
          <div>
            <h2>过滤审计</h2>
            <span>按 Run 追踪每只票在哪个环节被过滤，以及具体原因</span>
          </div>
          <button className="ghost-btn" type="button" onClick={() => void loadAudit()} disabled={!runId || loading}>
            {loading ? <Loader2 size={14} className="spin" /> : <RefreshCcw size={14} />}
            刷新
          </button>
        </div>
        <div className="form-grid compact-grid">
          <label className="field">
            <span>Run</span>
            <select value={runId} onChange={(event) => setRunId(event.target.value)}>
              {completedRuns.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {run.run_name} · {run.trade_date} · {run.recommended_count} 条
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>环节</span>
            <select value={stage} onChange={(event) => setStage(event.target.value)}>
              {stageOptions.map((item) => (
                <option key={item} value={item}>{item === ALL_STAGES ? "全部" : stageLabel(item)}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>状态</span>
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              {statusOptions.map((item) => (
                <option key={item} value={item}>{item === ALL_STATUS ? "全部" : statusLabel(item)}</option>
              ))}
            </select>
          </label>
          <label className="field audit-search-field">
            <span>搜索</span>
            <div className="audit-search-box">
              <Search size={14} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="代码 / 名称 / 原因" />
            </div>
          </label>
        </div>
        {error ? (
          <div className="bt-error">
            <AlertTriangle size={14} />
            {error}
          </div>
        ) : null}
      </section>

      {loading && !audit ? (
        <div className="loading-block">
          <Loader2 size={24} className="spin" />
          <span>正在加载过滤审计…</span>
        </div>
      ) : null}

      {audit ? (
        <>
          {audit.empty_reason ? <EmptyAudit text={audit.empty_reason} /> : null}
          <AuditStageSummary rows={audit.stage_summary} />
          <AuditDistributions distributions={audit.distributions} />
          <div className="audit-main-grid">
            <AuditTable rows={rows} total={audit.rows.length} selectedCode={selectedRow?.code ?? ""} onSelect={setSelectedCode} />
            <AuditRowDetail row={selectedRow} />
          </div>
        </>
      ) : !loading ? (
        <EmptyAudit text="暂无可审计 Run" />
      ) : null}
    </div>
  );
});
