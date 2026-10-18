import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, ListFilter } from "lucide-react";
import { api } from "../api/client";
import type { FunnelAuditRow, FunnelTraceStep, RunFunnelAudit } from "../types";
import { ErrorPage } from "./StateViews";
import { tableStyle, thStyle, tdStyle, fmtPrice } from "./recordsShared";
import { describeSoftFloor, type SoftFloorMeta } from "../utils/softFloor";

/**
 * 运行记录 — 股票分明细
 *
 * 解决「看不到具体股票的分数」：展示本次运行里走到 Skill 深审/分数线阶段的股票，
 * 每只给出阶段分数链（规则门 → 深审最终分 → 有效线 → 差距），并可展开看
 * 规则分量拆解 / Skill·LLM 深审细节 / 有效线惩罚明细。
 *
 * 数据来自 /api/runs/{run_id}/funnel 的 rows（后端已持久化 filter_audit，零后端改动）。
 * 默认只显示「深审卡」（走到 skill_review / score_line 阶段的 ~60 只），可切换到全部。
 */

/** 深审卡：走到 skill_review / score_line / recommended 阶段（有最终分） */
const DEEP_STAGES = new Set(["skill_review", "score_line", "recommended"]);

function isDeepRow(row: FunnelAuditRow): boolean {
  return DEEP_STAGES.has(row.final_stage) || (row.score_line?.final_score ?? 0) > 0;
}

function fmtNum(v: number | null | undefined, digits = 1): string {
  return v == null || Number.isNaN(v) ? "—" : v.toFixed(digits);
}

/** 从 trace 里取某阶段 step */
function stepOf(row: FunnelAuditRow, stage: string): FunnelTraceStep | undefined {
  return (row.trace || []).find((t) => t.stage === stage);
}

/** 该股的有效线惩罚明细（来自 score_line_meta / trace score_line step） */
function linePenalties(row: FunnelAuditRow): Array<{ label: string; value: number }> {
  const meta = row.score_line?.score_line_meta || {};
  const out: Array<{ label: string; value: number }> = [];
  const push = (label: string, v: unknown) => {
    const n = Number(v);
    if (Number.isFinite(n) && n > 0) out.push({ label, value: n });
  };
  push("恐慌日", meta.panic_day_penalty);
  push("弱势·低chg20", meta.regime_weak_low_chg20_penalty);
  push("震荡·低pos60", meta.regime_oscillation_low_pos_penalty);
  push("强势·MA支撑", meta.regime_support_ma5_penalty);
  push("强势阶段", (meta as Record<string, unknown>).regime_strong_penalty);
  push("证据不足", meta.evidence_adjustment);
  push("成交约束", meta.execution_adjustment);
  return out;
}

function softFloorMetaOf(row: FunnelAuditRow): SoftFloorMeta {
  const raw = (row.score_line?.score_line_meta || {}) as Record<string, unknown>;
  return {
    dynamic_soft_floor_applied: raw.dynamic_soft_floor_applied as SoftFloorMeta["dynamic_soft_floor_applied"],
    dynamic_soft_floor: raw.dynamic_soft_floor as number | null | undefined,
    soft_floor_gap: raw.soft_floor_gap as number | null | undefined,
    soft_floor_reason: raw.soft_floor_reason as string | null | undefined,
    market_base: raw.market_base as string | null | undefined,
    market_sentiment: raw.market_sentiment as string | null | undefined,
    degraded_agent_unavailable: raw.degraded_agent_unavailable as SoftFloorMeta["degraded_agent_unavailable"],
    effective_score_line: raw.effective_score_line as number | null | undefined,
    base_score_line: raw.base_score_line as number | null | undefined,
  };
}

export function RunStockScoreSection({ runId }: { runId: string }) {
  const [audit, setAudit] = useState<RunFunnelAudit | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api
      .runFunnelAudit(runId)
      .then((d) => setAudit(d))
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message || "加载失败");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [runId]);

  const rows = audit?.rows ?? [];
  const deepRows = useMemo(() => rows.filter(isDeepRow), [rows]);
  const shown = useMemo(() => {
    const list = showAll ? rows : deepRows;
    // 深审卡按最终分倒序；全部模式按最终阶段 + 分数
    return [...list].sort((a, b) => (b.score_line?.final_score ?? 0) - (a.score_line?.final_score ?? 0));
  }, [rows, deepRows, showAll]);

  if (loading) return <div className="skeleton" style={{ height: 160 }} />;
  if (error) return <ErrorPage title="股票分明细加载失败" message={error} onRetry={() => window.location.reload()} />;
  if (!rows.length) return null;

  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">股票分明细</div>
          <h3 className="panel__title">
            <ListFilter size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />
            {showAll ? `全部 ${rows.length} 只` : `深审 ${deepRows.length} 只`}
          </h3>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>规则分 / 深审分 / 有效线</span>
          <button
            className="btn btn--ghost"
            onClick={() => setShowAll((v) => !v)}
            style={{ fontSize: "var(--text-sm)", padding: "4px 10px" }}
          >
            {showAll ? "只看深审" : `展开全部 ${rows.length}`}
          </button>
        </div>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={{ ...thStyle, width: 24 }} aria-hidden />
              <th style={thStyle}>代码</th>
              <th style={thStyle}>名称</th>
              <th style={{ ...thStyle, textAlign: "right" }}>规则门分</th>
              <th style={{ ...thStyle, textAlign: "right" }}>深审最终分</th>
              <th style={{ ...thStyle, textAlign: "right" }}>有效线</th>
              <th style={{ ...thStyle, textAlign: "right" }}>差距</th>
              <th style={thStyle}>止步阶段</th>
              <th style={thStyle}>原因</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const isOpen = expanded === row.code;
              const gate = row.rule_gate || stepOf(row, "skill_gate") || {};
              const gateScore = row.rule_gate?.best_gate_score ?? stepOf(row, "skill_gate")?.best_gate_score;
              const finalScore = row.score_line?.final_score;
              const effLine = row.score_line?.effective_score_line;
              const gap = row.score_line?.score_gap;
              return (
                <ScoreRow
                  key={row.code}
                  row={row}
                  isOpen={isOpen}
                  gateScore={gateScore}
                  finalScore={finalScore}
                  effLine={effLine}
                  gap={gap}
                  onToggle={() => setExpanded(isOpen ? null : row.code)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ScoreRow({
  row,
  isOpen,
  gateScore,
  finalScore,
  effLine,
  gap,
  onToggle,
}: {
  row: FunnelAuditRow;
  isOpen: boolean;
  gateScore: number | null | undefined;
  finalScore: number | null | undefined;
  effLine: number | null | undefined;
  gap: number | null | undefined;
  onToggle: () => void;
}) {
  const gapColor = gap == null ? "var(--muted)" : gap >= 0 ? "var(--market-up)" : "var(--market-down)";
  const soft = describeSoftFloor(softFloorMetaOf(row));
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }} className="records-row--clickable" title="点击展开分数细节">
        <td style={{ ...tdStyle, color: "var(--muted)" }}>
          {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </td>
        <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>{row.code}</td>
        <td style={tdStyle}>
          {row.name || "—"}
          <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginLeft: 6 }}>{row.industry || ""}</span>
          {soft.applied ? (
            <span
              title={soft.title}
              style={{
                marginLeft: 6,
                fontSize: 10,
                padding: "1px 5px",
                borderRadius: 3,
                border: soft.banned
                  ? "1px solid color-mix(in srgb, var(--warning) 25%, transparent)"
                  : "1px solid var(--border)",
                color: soft.banned ? "var(--warning)" : "var(--muted)",
                fontWeight: soft.banned ? 600 : 400,
                letterSpacing: 0.3,
                whiteSpace: "nowrap",
              }}
            >
              {soft.badge}
            </span>
          ) : null}
        </td>
        <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>{fmtNum(gateScore)}</td>
        <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
          {fmtNum(finalScore)}
        </td>
        <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
          {fmtNum(effLine)}
        </td>
        <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)", color: gapColor }}>
          {gap == null ? "—" : `${gap >= 0 ? "+" : ""}${gap.toFixed(1)}`}
        </td>
        <td style={{ ...tdStyle, fontSize: "var(--text-sm)", color: "var(--ink-secondary)" }}>
          {stageLabel(row.final_stage)}
        </td>
        <td style={{ ...tdStyle, fontSize: "var(--text-sm)", color: "var(--muted)", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {row.final_reason || "—"}
        </td>
      </tr>
      {isOpen && <ExpandedDetail row={row} />}
    </>
  );
}

function stageLabel(stage: string): string {
  const map: Record<string, string> = {
    market_universe: "全市场",
    scope_recall: "范围候选",
    prefilter: "预过滤",
    skill_gate: "规则门",
    skill_review_limit: "深审容量",
    skill_review: "Skill 深审",
    score_line: "分数线",
    recommended: "已推荐",
  };
  return map[stage] || stage;
}

/** 展开详情：阶段分数链 + 规则分量拆解 + Skill/LLM 深审 + 有效线惩罚 */
function ExpandedDetail({ row }: { row: FunnelAuditRow }) {
  const sl = row.score_line || {};
  const rawScores = sl.raw_component_scores || (sl.score_breakdown?.raw_scores as Record<string, number> | undefined) || {};
  const penalties = linePenalties(row);
  const reviewIds = row.rule_gate?.review_skill_ids || stepOf(row, "skill_review")?.review_skill_ids || [];
  const gateScores = row.rule_gate?.gate_scores || {};
  const adjReasons = (sl.score_line_meta?.reasons as string[] | undefined) || stepOf(row, "score_line")?.adjustment_reasons || [];
  const llmFallback = adjReasons.includes("agent_unavailable_fallback");
  const soft = describeSoftFloor(softFloorMetaOf(row));

  return (
    <tr>
      <td colSpan={9} style={{ ...tdStyle, background: "var(--surface-soft)", padding: "var(--space-3) var(--space-4)" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "var(--space-4)" }}>
          {/* 阶段分数链 */}
          <DetailBlock title="阶段分数链">
            {(row.trace || []).map((t, i) => (
              <KV
                key={i}
                k={`${stageLabel(t.stage)} ${t.passed ? "✓" : "✗"}`}
                v={
                  t.final_score != null
                    ? `${fmtNum(t.final_score)} / 线 ${fmtNum(t.effective_score_line)}`
                    : t.best_gate_score != null
                      ? `门分 ${fmtNum(t.best_gate_score)}${t.prelim_score != null ? ` · 初筛 ${fmtNum(t.prelim_score)}` : ""}`
                      : t.reason || "—"
                }
              />
            ))}
          </DetailBlock>

          {/* 规则分量拆解 */}
          <DetailBlock title="规则分量拆解">
            {Object.keys(rawScores).length === 0 ? (
              <span style={{ color: "var(--muted)" }}>无分量数据</span>
            ) : (
              Object.entries(rawScores).map(([k, v]) => <KV key={k} k={k} v={fmtNum(Number(v))} />)
            )}
          </DetailBlock>

          {/* Skill / LLM 深审 */}
          <DetailBlock title="Skill / LLM 深审（LLM 仅供参考，不影响出票）">
            <KV k="深审 skill" v={reviewIds.length ? reviewIds.join(", ") : "—"} />
            {row.rule_gate?.best_skill_id && <KV k="最佳规则门" v={row.rule_gate.best_skill_id} />}
            {Object.keys(gateScores).length > 0 &&
              Object.entries(gateScores).map(([k, v]) => <KV key={k} k={`门分·${k}`} v={fmtNum(Number(v))} />)}
            <KV
              k="LLM 状态"
              v={llmFallback ? "降级" : "正常（不改分）"}
              accent={llmFallback ? "var(--market-down)" : undefined}
            />
          </DetailBlock>

          {/* 有效线惩罚明细 */}
          <DetailBlock title="有效线惩罚明细">
            <KV k="基础线" v={fmtNum((sl.score_line_meta?.base_score_line as number | undefined) ?? null)} />
            {penalties.length === 0 ? (
              <KV k="无附加惩罚" v="" />
            ) : (
              penalties.map((p, i) => <KV key={i} k={p.label} v={`+${p.value}`} accent="var(--market-down)" />)
            )}
            <KV k="有效线" v={fmtNum(sl.effective_score_line)} bold />
            <KV k="规则分（出票依据）" v={fmtNum(sl.final_score)} bold />
            {soft.applied ? (
              <KV
                k={soft.banned ? "软降线·污染" : "软降线"}
                v={soft.line}
                accent={soft.banned ? "var(--warning)" : "var(--muted)"}
              />
            ) : null}
          </DetailBlock>
        </div>

        {/* 风险/信息标签 */}
        {((sl.risk_tags && sl.risk_tags.length > 0) || (sl.info_tags && sl.info_tags.length > 0)) && (
          <div style={{ marginTop: "var(--space-3)", display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(sl.risk_tags || []).map((t, i) => (
              <Tag key={`r${i}`} text={t} color="var(--market-down)" />
            ))}
            {(sl.info_tags || []).map((t, i) => (
              <Tag key={`i${i}`} text={t} color="var(--accent)" />
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

function DetailBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontSize: "var(--text-sm)", color: "var(--muted)", fontWeight: 600, marginBottom: 2 }}>{title}</div>
      {children}
    </div>
  );
}

function KV({ k, v, accent, bold }: { k: string; v: string; accent?: string; bold?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: "var(--text-sm)" }}>
      <span style={{ color: "var(--muted)" }}>{k}</span>
      <span style={{ fontFamily: "var(--font-mono)", color: accent || "var(--text)", fontWeight: bold ? 600 : 400, textAlign: "right" }}>{v}</span>
    </div>
  );
}

function Tag({ text, color }: { text: string; color: string }) {
  return (
    <span
      style={{
        fontSize: "var(--text-sm)",
        color,
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
        padding: "2px 8px",
        borderRadius: 4,
      }}
    >
      {text}
    </span>
  );
}
