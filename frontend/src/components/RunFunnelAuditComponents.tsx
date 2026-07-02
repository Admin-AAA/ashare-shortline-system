import { AlertTriangle, CircleCheck, CircleX } from "lucide-react";
import type { FunnelAuditRow, FunnelStageSummary, FunnelTraceStep } from "../types";
import {
  COMPONENT_ORDER,
  asNumber,
  asRecord,
  asStringList,
  componentLabel,
  distributionLabel,
  fmt,
  shortBucket,
  shortReason,
  stageLabel,
  statusLabel,
  toneForStatus,
  traceMeta,
} from "./runFunnelAuditHelpers";

export function AuditStageSummary({ rows }: { rows: FunnelStageSummary[] }) {
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
            <small>过滤 {row.filtered_count} · 通过率 {formatRateLocal(row.pass_count, row.input_count)}</small>
            <ReasonChips counts={row.reason_counts ?? {}} />
          </div>
        ))}
      </div>
    </section>
  );
}

export function AuditDistributions({ distributions }: { distributions: Record<string, Record<string, number>> }) {
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

export function DistributionCard({ name, values }: { name: string; values: Record<string, number> }) {
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

export function AuditTable({
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

export function AuditRowDetail({ row }: { row: FunnelAuditRow | null }) {
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

export function ReasonChips({ counts }: { counts: Record<string, number> }) {
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

export function TraceStepCard({ step }: { step: FunnelTraceStep }) {
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

export function MiniMetric({ label, value, tone }: { label: string; value: string; tone?: "positive" | "warning" | "danger" }) {
  return (
    <div className={`audit-metric-card ${tone ? tone : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function ReasonSection({ title, values, tone }: { title: string; values: string[]; tone?: "warning" | "danger" }) {
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

export function EmptyAudit({ text }: { text: string }) {
  return (
    <div className="empty-hint">
      <AlertTriangle size={16} />
      <span>{text}</span>
    </div>
  );
}

// local helper to avoid circular import of formatRate
function formatRateLocal(passCount: number, inputCount: number) {
  if (!inputCount) return "0%";
  return `${((passCount / inputCount) * 100).toFixed(1)}%`;
}
