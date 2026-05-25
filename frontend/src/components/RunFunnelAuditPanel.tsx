import { memo, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CircleCheck, CircleX, Loader2, RefreshCcw, Search } from "lucide-react";
import { api } from "../api/client";
import type { FunnelAuditRow, FunnelStageSummary, FunnelTraceStep, RunFunnelAudit, RunSummary } from "../types";

type Props = {
  runs: RunSummary[];
};

const ALL_STAGES = "all";
const ALL_STATUS = "all";

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

function AuditStageSummary({ rows }: { rows: FunnelStageSummary[] }) {
  if (!rows.length) return null;
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>阶段分布</h2>
          <span>输入、通过、过滤数量按运行漏斗展开</span>
        </div>
      </div>
      <div className="audit-stage-grid">
        {rows.map((row) => (
          <div key={row.stage} className="audit-stage-card">
            <span>{row.label || stageLabel(row.stage)}</span>
            <strong>{row.pass_count} / {row.input_count}</strong>
            <small>过滤 {row.filtered_count} · 通过率 {formatRate(row.pass_count, row.input_count)}</small>
            <ReasonChips counts={row.reason_counts ?? {}} />
          </div>
        ))}
      </div>
    </section>
  );
}

function AuditDistributions({ distributions }: { distributions: Record<string, Record<string, number>> }) {
  const entries = Object.entries(distributions || {}).filter(([, values]) => Object.keys(values || {}).length);
  if (!entries.length) return null;
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>完整分布</h2>
          <span>环节、分数、证据、数据质量、分数组成与覆盖情况</span>
        </div>
      </div>
      <div className="audit-distribution-grid">
        {entries.map(([name, values]) => (
          <DistributionCard key={name} name={name} values={values} />
        ))}
      </div>
    </section>
  );
}

function DistributionCard({ name, values }: { name: string; values: Record<string, number> }) {
  const rawEntries = Object.entries(values);
  const positiveEntries = rawEntries.filter(([, count]) => Number(count) > 0);
  const entries = positiveEntries.length ? positiveEntries : rawEntries;
  const total = positiveEntries.reduce((sum, [, count]) => sum + Number(count), 0);
  const topEntries = entries.slice(0, 8);
  const maxCount = Math.max(...entries.map(([, count]) => Number(count)), 1);

  return (
    <div className="audit-distribution-card">
      <strong>{distributionLabel(name)}</strong>
      <div className="audit-distribution-total">{total} 项</div>
      <div className="audit-bar-list">
        {topEntries.map(([bucket, count]) => {
          const width = Number(count) > 0 ? Math.max(8, Math.round((Number(count) / maxCount) * 100)) : 0;
          return (
            <div key={bucket} className="audit-bar-row">
              <span title={bucket}>{shortBucket(bucket)}</span>
              <div className="audit-bar-track">
                <div className="audit-bar-fill" style={{ width: `${width}%` }} />
              </div>
              <strong>{count}</strong>
            </div>
          );
        })}
      </div>
      {entries.length > topEntries.length ? <small>另有 {entries.length - topEntries.length} 个分桶</small> : null}
    </div>
  );
}

function AuditTable({
  rows,
  total,
  selectedCode,
  onSelect,
}: {
  rows: FunnelAuditRow[];
  total: number;
  selectedCode: string;
  onSelect: (code: string) => void;
}) {
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>逐票路径</h2>
          <span>当前显示 {rows.length} / {total} 只</span>
        </div>
      </div>
      <div className="table-wrap audit-table-wrap">
        <table>
          <thead>
            <tr>
              <th>股票</th>
              <th>最终环节</th>
              <th>路径</th>
              <th>原因</th>
              <th>规则门</th>
              <th>最终分</th>
              <th>分数线</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={`${row.code}-${row.final_stage}`}
                className={row.code === selectedCode ? "selected" : ""}
                onClick={() => onSelect(row.code)}
              >
                <td>
                  <strong>{row.code}</strong>
                  <br />
                  <span>{row.name || "-"} · {row.industry || "-"}</span>
                </td>
                <td><span className="tag" data-tone={toneForStatus(row.final_status)}>{stageLabel(row.final_stage)}</span></td>
                <td>{row.trace_summary || "-"}</td>
                <td>{row.final_reason}</td>
                <td>
                  {row.rule_gate ? (
                    <>
                      <strong>{fmt(row.rule_gate.best_gate_score)}</strong>
                      <br />
                      <span>{row.rule_gate.best_skill_id || "-"}</span>
                    </>
                  ) : "-"}
                </td>
                <td>{fmt(row.score_line?.final_score)}</td>
                <td>{fmt(row.score_line?.effective_score_line)}</td>
                <td>
                  {row.score_line ? (
                    <>
                      <span>质量 {fmt(row.score_line.evidence_quality_score)}</span>
                      <br />
                      <span>完整 {fmt(row.score_line.data_completeness_score)}</span>
                    </>
                  ) : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AuditRowDetail({ row }: { row: FunnelAuditRow | null }) {
  if (!row) {
    return (
      <section className="panel audit-detail-panel">
        <div className="panel-title">
          <div>
            <h2>票路径详情</h2>
            <span>选中一只票后，这里会展开完整过滤过程</span>
          </div>
        </div>
        <EmptyAudit text="当前没有可展开的票" />
      </section>
    );
  }

  const score = row.score_line;
  const trace = row.trace ?? [];
  const rawScores = score?.raw_component_scores ?? {};
  const weightedScores = score?.weighted_component_scores ?? {};
  const weights = score?.aggregation_weights ?? {};
  const dataQuality = asRecord(score?.data_quality);
  const missingFields = asStringList(dataQuality.missing_fields);
  const sourceBreakdown = asRecord(dataQuality.source_breakdown);
  const riskTags = score?.risk_tags ?? [];
  const infoTags = score?.info_tags ?? [];
  const gateScores = row.rule_gate?.gate_scores ?? {};

  return (
    <section className="panel audit-detail-panel">
      <div className="panel-title">
        <div>
          <h2>{row.code}</h2>
          <span>{row.name || "-"} · {row.industry || "-"} · {stageLabel(row.final_stage)}</span>
        </div>
        <span className="tag" data-tone={toneForStatus(row.final_status)}>{statusLabel(row.final_status)}</span>
      </div>

      <div className="audit-detail-summary">
        <MiniMetric label="最终分" value={fmt(score?.final_score)} tone={score?.passed ? "positive" : "warning"} />
        <MiniMetric label="有效线" value={fmt(score?.effective_score_line)} />
        <MiniMetric label="分差" value={fmt(score?.score_gap)} tone={score && (score.score_gap ?? 0) >= 0 ? "positive" : "danger"} />
        <MiniMetric label="证据" value={fmt(score?.evidence_quality_score)} />
        <MiniMetric label="完整度" value={fmt(score?.data_completeness_score)} />
        <MiniMetric label="真实性" value={fmt(score?.data_authenticity)} />
      </div>

      <section className="audit-detail-section">
        <div className="detail-section-head">
          <h3>过滤路径</h3>
          <span>{row.trace_summary || "未生成路径摘要"}</span>
        </div>
        <div className="audit-trace-grid">
          {trace.length ? trace.map((step) => <TraceStepCard key={`${step.stage}-${step.label}`} step={step} />) : <EmptyAudit text="该票没有路径明细" />}
        </div>
      </section>

      <section className="audit-detail-section">
        <div className="detail-section-head">
          <h3>规则门</h3>
          <span>看这只票为什么进或没进深审</span>
        </div>
        <div className="audit-matrix-grid">
          <MiniMetric label="预评分" value={fmt(row.rule_gate?.prelim_score)} />
          <MiniMetric label="最佳门分" value={fmt(row.rule_gate?.best_gate_score)} />
          <MiniMetric label="最佳 Skill" value={row.rule_gate?.best_skill_id || "-"} />
          <MiniMetric label="是否选入" value={row.rule_gate?.review_selected === false ? "否" : "是"} tone={row.rule_gate?.review_selected === false ? "danger" : "positive"} />
        </div>
        {Object.keys(gateScores).length ? (
          <div className="pill-row audit-chip-row">
            {Object.entries(gateScores).map(([skillId, value]) => (
              <span key={skillId} className="pill static">{skillId}: {fmt(value)}</span>
            ))}
          </div>
        ) : null}
      </section>

      <section className="audit-detail-section">
        <div className="detail-section-head">
          <h3>分数组成</h3>
          <span>原始分、权重与加权贡献</span>
        </div>
        <div className="audit-matrix-grid">
          <MiniMetric label="共识放大" value={fmt(score?.consensus_boost)} />
          <MiniMetric label="质量上限" value={fmt(score?.quality_cap)} />
          <MiniMetric label="样本真实性" value={fmt(score?.data_authenticity)} />
          <MiniMetric label="深审数" value={String(score?.strategy_signal_count ?? 0)} />
        </div>
        <table className="audit-component-table">
          <thead>
            <tr>
              <th>组件</th>
              <th>原始分</th>
              <th>权重</th>
              <th>加权贡献</th>
            </tr>
          </thead>
          <tbody>
            {COMPONENT_ORDER.map((key) => (
              <tr key={key}>
                <td>{componentLabel(key)}</td>
                <td>{fmt(asNumber(rawScores[key]))}</td>
                <td>{fmt(asNumber(weights[key]))}</td>
                <td>{fmt(asNumber(weightedScores[key]))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="audit-detail-section">
        <div className="detail-section-head">
          <h3>证据与覆盖</h3>
          <span>缺失项和数据来源会直接压低分数</span>
        </div>
        <div className="audit-matrix-grid">
          <MiniMetric label="证据质量" value={fmt(score?.evidence_quality_score)} />
          <MiniMetric label="完整度" value={fmt(score?.data_completeness_score)} />
          <MiniMetric label="真实性" value={fmt(score?.data_authenticity)} />
          <MiniMetric label="来源数" value={String(Object.keys(sourceBreakdown).length)} />
        </div>
        {missingFields.length ? <ReasonSection title="缺失字段" values={missingFields} /> : null}
        {Object.keys(sourceBreakdown).length ? <ReasonSection title="来源分布" values={Object.entries(sourceBreakdown).map(([key, value]) => `${key} x ${value}`)} /> : null}
        {riskTags.length ? <ReasonSection title="风险标签" values={riskTags} tone="danger" /> : null}
        {infoTags.length ? <ReasonSection title="提示标签" values={infoTags} tone="warning" /> : null}
      </section>
    </section>
  );
}

function ReasonChips({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, count]) => Number(count) > 0).slice(0, 3);
  if (!entries.length) return null;
  return (
    <div className="pill-row audit-reason-row">
      {entries.map(([reason, count]) => (
        <span key={reason} className="pill static">{shortReason(reason)} x {count}</span>
      ))}
    </div>
  );
}

function TraceStepCard({ step }: { step: FunnelTraceStep }) {
  return (
    <div className={`audit-trace-card ${step.passed ? "pass" : "fail"}`}>
      <div className="audit-trace-head">
        <span className="audit-trace-state">
          {step.passed ? <CircleCheck size={14} /> : <CircleX size={14} />}
          {step.label || stageLabel(step.stage)}
        </span>
        <strong>{step.passed ? "通过" : "过滤"}</strong>
      </div>
      <p>{step.reason || "-"}</p>
      <small>{traceMeta(step)}</small>
    </div>
  );
}

function MiniMetric({ label, value, tone }: { label: string; value: string; tone?: "positive" | "warning" | "danger" }) {
  return (
    <div className={`audit-metric-card ${tone ? tone : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReasonSection({ title, values, tone }: { title: string; values: string[]; tone?: "warning" | "danger" }) {
  if (!values.length) return null;
  return (
    <div className="audit-reason-section">
      <span>{title}</span>
      <div className="pill-row">
        {values.map((value) => (
          <span key={value} className="pill static" data-tone={tone || "neutral"}>
            {value}
          </span>
        ))}
      </div>
    </div>
  );
}

function EmptyAudit({ text }: { text: string }) {
  return (
    <div className="empty-hint">
      <AlertTriangle size={16} />
      <span>{text}</span>
    </div>
  );
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    market_universe: "全市场",
    scope_recall: "范围召回",
    prefilter: "预过滤",
    prefilter_common: "公共预过滤",
    prefilter_skill: "Skill 预过滤",
    prefilter_pass: "预过滤通过",
    rule_scan: "规则扫描",
    skill_gate: "规则门",
    skill_review_limit: "深审容量",
    skill_review: "Skill 深审",
    score_line: "分数线",
    recommended: "已推荐",
  };
  return labels[stage] ?? stage;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    active: "继续流转",
    filtered: "已过滤",
    recommended: "已推荐",
  };
  return labels[status] ?? status;
}

function distributionLabel(name: string) {
  const labels: Record<string, string> = {
    final_stage: "停止环节",
    final_status: "最终状态",
    industry: "行业",
    prefilter_stage: "预过滤环节",
    prefilter_source: "预过滤来源",
    skill_gate_result: "规则门结果",
    skill_gate_reason: "规则门原因",
    score_line_result: "分数线结果",
    score_line_adjustment_reason: "分线惩罚",
    setup_type: "买点类型",
    market_regime: "市场环境",
    prelim_score: "预评分",
    best_gate_score: "规则门分",
    final_score_all: "候选最终分",
    final_score_filtered: "已过滤最终分",
    final_score_recommended: "推荐最终分",
    effective_score_line: "有效分数线",
    score_gap: "距分数线",
    evidence_quality_all: "证据质量",
    data_completeness_all: "数据完整度",
    data_authenticity_all: "数据真实性",
    quality_cap: "质量上限",
    consensus_boost: "共识放大",
    score_line_adjustment: "证据惩罚",
    risk_tag_count: "风险标签数",
    info_tag_count: "提示标签数",
    risk_tags: "风险标签",
    info_tags: "提示标签",
    missing_fields: "缺失字段",
    data_source_breakdown: "来源分布",
    pct: "涨跌幅",
    amount_yi: "成交额",
    turnover_rate: "换手率",
    raw_skill_agent: "原始 Agent 分",
    raw_strategy_rule: "原始规则分",
    raw_market: "原始市场分",
    raw_risk: "原始风险分",
    raw_evidence: "原始证据分",
    weighted_skill_agent: "加权 Agent 贡献",
    weighted_strategy_rule: "加权规则贡献",
    weighted_market: "加权市场贡献",
    weighted_risk: "加权风险贡献",
    weighted_evidence: "加权证据贡献",
  };
  return labels[name] ?? name;
}

function toneForStatus(status: string) {
  if (status === "recommended") return "positive";
  if (status === "filtered") return "danger";
  return "warning";
}

function shortReason(reason: string) {
  return reason.length > 18 ? `${reason.slice(0, 18)}...` : reason;
}

function shortBucket(value: string) {
  return value.length > 16 ? `${value.slice(0, 16)}...` : value;
}

function fmt(value: number | null | undefined) {
  return value == null || Number.isNaN(Number(value)) ? "-" : Number(value).toFixed(1);
}

function formatRate(passCount: number, inputCount: number) {
  if (!inputCount) return "0%";
  return `${((passCount / inputCount) * 100).toFixed(1)}%`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function traceMeta(step: FunnelTraceStep) {
  const parts: string[] = [];
  if (step.final_score != null) parts.push(`最终分 ${fmt(step.final_score)}`);
  if (step.effective_score_line != null) parts.push(`分线 ${fmt(step.effective_score_line)}`);
  if (step.score_gap != null) parts.push(`分差 ${fmt(step.score_gap)}`);
  if (step.best_gate_score != null) parts.push(`门分 ${fmt(step.best_gate_score)}`);
  if (step.prelim_score != null) parts.push(`预分 ${fmt(step.prelim_score)}`);
  if (step.adjustment_reasons?.length) parts.push(step.adjustment_reasons.join("、"));
  return parts.join(" · ");
}

const COMPONENT_ORDER = ["skill_agent", "strategy_rule", "market", "risk", "evidence"] as const;

function componentLabel(key: string) {
  const labels: Record<string, string> = {
    skill_agent: "Agent",
    strategy_rule: "规则",
    market: "市场",
    risk: "风险",
    evidence: "证据",
  };
  return labels[key] ?? key;
}
