import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Loader2,
  PlayCircle,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { api } from "../api/client";
import type {
  DataDomainCoverage,
  DataDomainCategory,
  DataDomainCoverageSummary,
  DataHealthDiagnostic,
  DataHealthRemediationResult,
  DataSourceHealthSummary,
  ScheduledTaskRun,
} from "../types";
import { CardSkeleton, EmptyState, ErrorPage } from "../components/StateViews";
import { Panel } from "../components/Panel";

/**
 * 数据健康度 —— 配置页「数据健康」Tab（与预设管理平级）。
 *
 * 展示全部数据表覆盖情况、全部问题、参考交易日来源，
 * 并提供「触发修复」按钮（异步任务 + 状态轮询，复用调度任务的 result 字段）。
 */
export function DataHealthSection() {
  const [diag, setDiag] = useState<DataHealthDiagnostic | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 修复任务运行状态（startDataHealthRemediation 返回 task_run；轮询 status）
  const [remediationTaskRun, setRemediationTaskRun] = useState<ScheduledTaskRun | null>(null);
  const [remediationStarting, setRemediationStarting] = useState(false);
  const [remediationError, setRemediationError] = useState("");
  const [remediationResult, setRemediationResult] = useState<DataHealthRemediationResult | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadDiag = useCallback((opts?: { forceRefresh?: boolean }, signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    api
      .dataHealth({ signal, forceRefresh: opts?.forceRefresh })
      .then((d) => setDiag(d))
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message || "诊断失败");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadDiag(undefined, controller.signal);
    // 进入页面时同步一下是否有进行中的修复任务
    api
      .dataHealthRemediationStatus()
      .then(({ task_run }) => {
        if (task_run && (task_run.status === "running" || task_run.status === "pending")) {
          setRemediationTaskRun(task_run);
        }
      })
      .catch(() => {});
    return () => {
      controller.abort();
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [loadDiag]);

  // 修复任务轮询
  useEffect(() => {
    if (!remediationTaskRun) return;
    if (remediationTaskRun.status !== "running" && remediationTaskRun.status !== "pending") return;
    pollRef.current = window.setTimeout(async () => {
      try {
        const { task_run } = await api.dataHealthRemediationStatus();
        if (task_run && task_run.task_run_id === remediationTaskRun.task_run_id) {
          setRemediationTaskRun(task_run);
          // 完成后从 result 里取修复对比结果 + 刷新诊断
          if (task_run.status === "completed" || task_run.status === "failed" || task_run.status === "partial_completed") {
            const result = task_run.result as unknown as DataHealthRemediationResult | undefined;
            if (result && typeof result === "object" && result.status) {
              setRemediationResult(result);
            }
            // 修复已写入数据，强制刷新缓存（后端 remediate 也已 clear，双保险）
            loadDiag({ forceRefresh: true });
          }
        }
      } catch {
        /* 轮询失败静默，下一轮重试 */
      }
    }, 3000);
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [remediationTaskRun, loadDiag]);

  const handleStartRemediation = async () => {
    setRemediationStarting(true);
    setRemediationError("");
    setRemediationResult(null);
    try {
      const { task_run } = await api.startDataHealthRemediation({ post_diagnosis: true });
      if (task_run) {
        setRemediationTaskRun(task_run);
      } else {
        // 同步完成的极短情况：强制刷新诊断
        loadDiag({ forceRefresh: true });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "触发修复失败";
      setRemediationError(msg);
    } finally {
      setRemediationStarting(false);
    }
  };

  if (loading && !diag) {
    return <CardSkeleton count={2} />;
  }
  if (error) {
    return (
      <ErrorPage
        title="数据健康度诊断失败"
        message={error}
        onRetry={() => loadDiag({ forceRefresh: true })}
      />
    );
  }
  if (!diag) {
    return <EmptyState title="暂无诊断数据" reason="后端数据健康度诊断尚未就绪" />;
  }

  const score = diag.score ?? 0;
  const scoreColor = score >= 90 ? "var(--market-up)" : score >= 70 ? "var(--warning)" : "var(--market-down)";
  const scoreLabel = score >= 90 ? "健康" : score >= 70 ? "需关注" : "异常";
  const refDate = diag.reference_trade_date || "—";
  const refSource = diag.reference_trade_date_source || "";
  const refNote = diag.reference_trade_date_note || "";
  const tables = diag.tables || {};
  const lagDays = diag.lag_days || {};
  const issues = diag.issues || [];
  const highIssues = issues.filter((i) => i.severity === "high");
  const mediumIssues = issues.filter((i) => i.severity === "medium");
  const lowIssues = issues.filter((i) => i.severity === "low" || (i.severity !== "high" && i.severity !== "medium"));

  const dsSummary = diag.data_sources;
  const dsTitle = dsSummary
    ? `通 ${dsSummary.working_count ?? 0} · 不通 ${dsSummary.broken_count ?? 0} · 未测试 ${dsSummary.untested_count ?? 0} · 未启用 ${dsSummary.disabled_count ?? 0}`
    : "加载中…";

  const domSummary = diag.data_domains;

  const tableRows = Object.entries(tables)
    .map(([key, info]) => ({
      key,
      label: info?.label || key,
      table: info?.table || key,
      maxDate: info?.max_date || "—",
      minDate: info?.min_date || "—",
      lag: lagDays[key] ?? null,
      critical: info?.critical ?? false,
      totalRows: info?.total_rows ?? 0,
      error: info?.error ?? null,
    }))
    .sort((a, b) => {
      if (a.critical !== b.critical) return a.critical ? -1 : 1;
      const aLag = a.lag ?? 999;
      const bLag = b.lag ?? 999;
      return bLag - aLag;
    });

  const isRunning =
    remediationTaskRun != null &&
    (remediationTaskRun.status === "running" || remediationTaskRun.status === "pending");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {/* 评分概览 + 修复操作 */}
      <Panel
        kicker="数据健康度"
        title={
          <>
            <Database size={16} />
            {score} 分 · {scoreLabel}
          </>
        }
        actions={
          <button
            className="btn btn--primary"
            disabled={isRunning || remediationStarting}
            onClick={handleStartRemediation}
          >
            {isRunning || remediationStarting ? <Loader2 size={14} className="spin" /> : <PlayCircle size={14} />}
            {isRunning ? "修复中…" : remediationStarting ? "提交中…" : "触发数据修复"}
          </button>
        }
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-4)", marginBottom: "var(--space-3)" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", minWidth: 64 }}>
            <span className="text-mono" style={{ fontSize: "var(--text-3xl)", fontWeight: 700, color: scoreColor }}>
              {score}
            </span>
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>健康评分</span>
          </div>
          <div style={{ flex: 1, fontSize: "var(--text-sm)", color: "var(--ink-secondary)" }}>
            <div>
              参考交易日 <strong className="text-mono">{refDate}</strong>
              {refSource && <span className="text-muted"> ({refSource})</span>}
            </div>
            {refNote && <div className="text-muted" style={{ marginTop: 2 }}>{refNote}</div>}
            <div className="text-muted" style={{ marginTop: 2 }}>
              {highIssues.length > 0 && <span style={{ color: "var(--market-down)" }}>高 {highIssues.length} </span>}
              {mediumIssues.length > 0 && <span style={{ color: "var(--warning)" }}>中 {mediumIssues.length} </span>}
              {lowIssues.length > 0 && <span>低 {lowIssues.length} </span>}
              共 {issues.length} 个问题
            </div>
          </div>
        </div>

        <RemediationStatusBanner
          taskRun={remediationTaskRun}
          starting={remediationStarting}
          error={remediationError}
          result={remediationResult}
        />
      </Panel>

      {/* 各表覆盖情况 —— 全量 */}
      {tableRows.length > 0 && (
        <Panel
          kicker="数据表覆盖"
          title={`共 ${tableRows.length} 张表`}
          actions={
            <button className="btn btn--ghost" onClick={() => loadDiag({ forceRefresh: true })} title="重新诊断（跳过缓存）">
              <RefreshCw size={14} />
            </button>
          }
        >
          <div style={{ overflowX: "auto" }}>
            <table className="data-table" style={{ width: "100%", fontSize: "var(--text-sm)", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--text-muted)", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "var(--space-2)", fontWeight: 500 }}>数据表</th>
                  <th style={{ padding: "var(--space-2)", fontWeight: 500 }}>起始日期</th>
                  <th style={{ padding: "var(--space-2)", fontWeight: 500 }}>最新日期</th>
                  <th style={{ padding: "var(--space-2)", fontWeight: 500, textAlign: "right" }}>行数</th>
                  <th style={{ padding: "var(--space-2)", fontWeight: 500, textAlign: "right" }}>滞后</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row) => {
                  const lagColor =
                    row.lag == null
                      ? "var(--text-muted)"
                      : row.lag <= 0
                        ? "var(--market-up)"
                        : row.lag <= 2
                          ? "var(--warning)"
                          : "var(--market-down)";
                  return (
                    <tr key={row.key} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "var(--space-2)" }}>
                        {row.critical && <span style={{ color: "var(--accent-strong)" }}>● </span>}
                        {row.label}
                        {row.error && (
                          <span className="text-muted" style={{ marginLeft: 6 }} title={row.error}>
                            ⚠
                          </span>
                        )}
                      </td>
                      <td className="text-mono" style={{ padding: "var(--space-2)", color: "var(--ink-secondary)" }}>
                        {row.minDate}
                      </td>
                      <td className="text-mono" style={{ padding: "var(--space-2)", color: "var(--ink-secondary)" }}>
                        {row.maxDate}
                      </td>
                      <td className="text-mono" style={{ padding: "var(--space-2)", textAlign: "right", color: "var(--ink-secondary)" }}>
                        {row.totalRows.toLocaleString()}
                      </td>
                      <td className="text-mono" style={{ padding: "var(--space-2)", textAlign: "right", color: lagColor }}>
                        {row.lag == null ? "—" : `${row.lag}d`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {/* 数据域接入覆盖 —— 指标级：哪些数据域有接入策略且持续拉取 */}
      {domSummary && (
        <Panel
          kicker="数据域覆盖"
          title={
            <>
              已接入 {domSummary.covered_count} · 滞后 {domSummary.stale_count} · 空 {domSummary.empty_count} · 覆盖率 {Math.round(domSummary.coverage_rate * 100)}%
            </>
          }
          actions={
            domSummary.score_penalty ? (
              <span className="text-muted" style={{ fontSize: "var(--text-sm)" }}>
                评分 −{domSummary.score_penalty}
              </span>
            ) : (
              <span style={{ fontSize: "var(--text-sm)", color: "#16a34a" }}>全覆盖</span>
            )
          }
        >
          <DomainCoverageSection summary={domSummary} />
        </Panel>
      )}

      {/* 数据源接入 —— 通 / 不通 / 未测试 / 未启用，计入健康评分 */}
      {dsSummary && (
        <Panel
          kicker="数据源接入"
          title={dsTitle}
          actions={
            dsSummary.score_penalty ? (
              <span className="text-muted" style={{ fontSize: "var(--text-sm)" }}>
                评分 −{dsSummary.score_penalty}
              </span>
            ) : (
              <span style={{ fontSize: "var(--text-sm)", color: "var(--market-down)" }}>全部通畅</span>
            )
          }
        >
          <DataSourceSection summary={dsSummary} />
        </Panel>
      )}

      {/* 问题列表 —— 全量，按严重度分组 */}
      {issues.length > 0 && (
        <Panel kicker="诊断问题" title={`共 ${issues.length} 个`}>
          <IssueList title="高优先级" tone="down" icon={<XCircle size={14} />} items={highIssues} />
          <IssueList title="中优先级" tone="warn" icon={<AlertTriangle size={14} />} items={mediumIssues} />
          <IssueList title="低优先级" tone="muted" icon={<CheckCircle2 size={14} />} items={lowIssues} />
        </Panel>
      )}

      {issues.length === 0 && tableRows.length === 0 && (
        <EmptyState title="无诊断数据" reason="诊断未返回任何表或问题" />
      )}
    </div>
  );
}

// ─── 修复状态横幅 ─────────────────────────────────────────────────
function RemediationStatusBanner({
  taskRun,
  starting,
  error,
  result,
}: {
  taskRun: ScheduledTaskRun | null;
  starting: boolean;
  error: string;
  result: DataHealthRemediationResult | null;
}) {
  if (starting) {
    return (
      <Banner tone="info">
        <Loader2 size={14} className="spin" />
        正在提交修复任务…
      </Banner>
    );
  }
  if (error) {
    return (
      <Banner tone="down">
        <XCircle size={14} />
        {error}
      </Banner>
    );
  }
  if (!taskRun) return null;

  const status = taskRun.status;
  if (status === "running" || status === "pending") {
    return (
      <Banner tone="info">
        <Loader2 size={14} className="spin" />
        修复任务运行中 · {taskRun.task_name}
        {taskRun.summary && <span className="text-muted"> — {taskRun.summary}</span>}
      </Banner>
    );
  }
  if (status === "completed" || status === "partial_completed") {
    const cmp = result?.comparison;
    const summary = result?.action_summary;
    return (
      <Banner tone="up">
        <CheckCircle2 size={14} />
        修复完成 · {taskRun.task_name}
        {summary && (
          <span className="text-muted">
            {" "}（计划 {summary.planned_count ?? 0} · 成功 {summary.completed_count ?? 0} · 失败 {summary.failed_count ?? 0}）
          </span>
        )}
        {cmp && cmp.score_delta != null && (
          <span className="text-mono" style={{ marginLeft: 8 }}>
            评分 {cmp.score_delta >= 0 ? "+" : ""}{cmp.score_delta}
          </span>
        )}
        {taskRun.error_message && <div className="text-muted" style={{ marginTop: 4 }}>{taskRun.error_message}</div>}
      </Banner>
    );
  }
  if (status === "failed" || status === "skipped") {
    return (
      <Banner tone="down">
        <XCircle size={14} />
        修复{status === "skipped" ? "已跳过" : "失败"} · {taskRun.task_name}
        {taskRun.error_message && <div className="text-muted" style={{ marginTop: 4 }}>{taskRun.error_message}</div>}
      </Banner>
    );
  }
  return null;
}

function Banner({ tone, children }: { tone: "up" | "down" | "info" | "warn"; children: React.ReactNode }) {
  const color =
    tone === "up"
      ? "var(--market-up)"
      : tone === "down"
        ? "var(--market-down)"
        : tone === "warn"
          ? "var(--warning)"
          : "var(--accent-strong)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        marginTop: "var(--space-3)",
        padding: "var(--space-2) var(--space-3)",
        background: "var(--surface-soft)",
        border: "1px solid var(--line)",
        borderLeft: `3px solid ${color}`,
        borderRadius: "var(--radius-md)",
        fontSize: "var(--text-sm)",
        color: "var(--ink-secondary)",
        flexWrap: "wrap",
      }}
    >
      {children}
    </div>
  );
}

// ─── 问题列表分组 ─────────────────────────────────────────────────
function IssueList({
  title,
  tone,
  icon,
  items,
}: {
  title: string;
  tone: "down" | "warn" | "muted";
  icon: React.ReactNode;
  items: Array<{ severity: string; message: string }>;
}) {
  if (items.length === 0) return null;
  const color = tone === "down" ? "var(--market-down)" : tone === "warn" ? "var(--warning)" : "var(--text-muted)";
  return (
    <div style={{ marginBottom: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", color, fontSize: "var(--text-sm)", fontWeight: 600, marginBottom: "var(--space-1)" }}>
        {icon}
        {title} · {items.length}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        {items.map((issue, idx) => (
          <div
            key={idx}
            style={{
              fontSize: "var(--text-sm)",
              lineHeight: 1.5,
              color: "var(--ink-secondary)",
              padding: "var(--space-1) var(--space-2)",
              background: "var(--surface-soft)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            {issue.message}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── 数据源接入分组 ───────────────────────────────────────────────
type SourceEntry = {
  source: string;
  source_type: string;
  status: string;
  enabled?: boolean;
  last_error?: string | null;
};

function SourceRow({ entry, tone }: { entry: SourceEntry; tone: "up" | "down" | "warn" | "muted" }) {
  const color =
    tone === "up" ? "#16a34a" : tone === "down" ? "#dc2626" : tone === "warn" ? "#d97706" : "var(--text-muted)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-1) var(--space-2)",
        background: "var(--surface-soft)",
        borderRadius: "var(--radius-sm)",
        fontSize: "var(--text-sm)",
        color: "var(--ink-secondary)",
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, flex: "0 0 auto" }} />
      <span className="text-mono" style={{ fontWeight: 600 }}>
        {entry.source}
      </span>
      <span className="text-muted">{entry.source_type}</span>
      {entry.last_error && tone === "down" && (
        <span
          className="text-muted"
          style={{ marginLeft: "auto", maxWidth: "55%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          title={entry.last_error}
        >
          {entry.last_error}
        </span>
      )}
    </div>
  );
}

function SourceGroup({ label, tone, items }: { label: string; tone: "up" | "down" | "warn" | "muted"; items?: SourceEntry[] }) {
  if (!items || items.length === 0) return null;
  const color =
    tone === "up" ? "#16a34a" : tone === "down" ? "#dc2626" : tone === "warn" ? "#d97706" : "var(--text-muted)";
  return (
    <div style={{ marginBottom: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--text-sm)", fontWeight: 600, color, marginBottom: "var(--space-1)" }}>
        {label} · {items.length}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {items.map((it, idx) => (
          <SourceRow key={idx} entry={it} tone={tone} />
        ))}
      </div>
    </div>
  );
}

function DataSourceSection({ summary }: { summary: DataSourceHealthSummary }) {
  return (
    <div>
      <div className="text-muted" style={{ fontSize: "var(--text-sm)", marginBottom: "var(--space-2)" }}>
        共 {summary.total ?? 0} 个数据源接入策略：
        <span style={{ color: "#16a34a" }}> {summary.working_count ?? 0} 个通畅</span>
        {summary.broken_count ? (
          <>,<span style={{ color: "#dc2626" }}> {summary.broken_count} 个不通（已扣分）</span></>
        ) : null}
        {summary.untested_count ? <>, {summary.untested_count} 个未测试</> : null}
        {summary.disabled_count ? <>, {summary.disabled_count} 个未启用</> : null}。
      </div>
      <SourceGroup label="通" tone="up" items={summary.working_sources} />
      <SourceGroup label="不通" tone="down" items={summary.broken_sources} />
      <SourceGroup label="未测试" tone="warn" items={summary.untested_sources} />
      <SourceGroup label="未启用" tone="muted" items={summary.disabled_sources} />
    </div>
  );
}

// ─── 数据域接入覆盖分组 ───────────────────────────────────────────
const DOMAIN_STATUS_META: Record<string, { color: string; label: string }> = {
  covered: { color: "#16a34a", label: "已接入" },
  stale: { color: "#d97706", label: "滞后" },
  empty: { color: "#dc2626", label: "空" },
  error: { color: "#dc2626", label: "异常" },
};

function DomainRow({ d }: { d: DataDomainCoverage }) {
  const meta = DOMAIN_STATUS_META[d.status] ?? DOMAIN_STATUS_META.empty;
  const gapText =
    d.status === "stale" && d.gap_days != null
      ? `滞后 ${d.gap_days}d`
      : d.fresh_window_days != null && d.status === "covered"
        ? `窗口 ${d.fresh_window_days}d`
        : "";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-1) var(--space-2)",
        background: "var(--surface-soft)",
        borderRadius: "var(--radius-sm)",
        fontSize: "var(--text-sm)",
        color: "var(--ink-secondary)",
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: meta.color, flex: "0 0 auto" }} />
      <span style={{ fontWeight: 600 }}>{d.label}</span>
      {d.critical && <span className="text-muted" title="关键数据域">·关</span>}
      <span className="text-muted" style={{ marginLeft: "auto" }}>
        {d.max_date || "—"}
      </span>
      <span className="text-mono" style={{ minWidth: 56, textAlign: "right" }}>
        {d.total_rows.toLocaleString()} 行
      </span>
      {gapText && (
        <span style={{ minWidth: 64, textAlign: "right", color: meta.color }}>{gapText}</span>
      )}
    </div>
  );
}

function DomainCategoryGroup({ cat }: { cat: DataDomainCategory }) {
  const hasGap = cat.stale_count + cat.empty_count > 0;
  return (
    <div style={{ marginBottom: "var(--space-3)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-1)",
          fontSize: "var(--text-sm)",
          fontWeight: 600,
          color: hasGap ? "var(--warning)" : "var(--ink-secondary)",
          marginBottom: "var(--space-1)",
        }}
      >
        {cat.category}
        <span className="text-muted" style={{ fontWeight: 400 }}>
          （{cat.covered_count} 已接入
          {cat.stale_count ? ` · ${cat.stale_count} 滞后` : ""}
          {cat.empty_count ? ` · ${cat.empty_count} 空` : ""}）
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {cat.domains.map((d) => (
          <DomainRow key={d.key} d={d} />
        ))}
      </div>
    </div>
  );
}

function DomainCoverageSection({ summary }: { summary: DataDomainCoverageSummary }) {
  return (
    <div>
      <div className="text-muted" style={{ fontSize: "var(--text-sm)", marginBottom: "var(--space-2)" }}>
        共 {summary.total} 个数据域（指标级）。覆盖率按「有数据且在刷新窗口内」计算；
        <span style={{ color: "#d97706" }}> 滞后</span> = 接入过但策略未持续刷新，
        <span style={{ color: "#dc2626" }}> 空</span> = 无数据（未接入 / 未拉取）。
      </div>
      {summary.categories.map((cat) => (
        <DomainCategoryGroup key={cat.category} cat={cat} />
      ))}
    </div>
  );
}
