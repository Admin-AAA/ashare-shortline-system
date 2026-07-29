/**
 * RecordsPage 股票分析详情 —— 从 RecordsPage.tsx 抽离（T10 重构）。
 * StockAnalysisDetailView + FeatureSnapshotSection + OutcomeSection +
 * ScoreBreakdownSection + ReviewNoteSection + numOr。
 */
import { useState } from "react";
import { ArrowLeft, Loader2, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import type { ExecutionConfirmation, ExecutionDecision, RecommendationDetail, RecommendationOutcome } from "../types";
import { ErrorPage, PageSkeleton } from "./StateViews";
import { RecommendationDetailCard } from "./RecommendationDetailCard";
import { TicketScoreBadge } from "./ScoreLineCell";
import { Panel } from "./Panel";
import {
  Metric,
  ReturnCell,
  ReturnStat,
  StatusBadge,
  fmtPrice,
  formatTime,
  tableStyle,
  thStyle,
  tdStyle,
  rowStyle,
} from "./recordsShared";
import { useApi } from "../app/useApi";
import { bucketLabel, exitReasonLabel, outcomeLabel, outcomeTone } from "../utils/display";

export function StockAnalysisDetailView({ recId, onBack }: { recId: string; onBack: () => void }) {
  const { data, loading, error } = useApi<RecommendationDetail>(
    () => api.recommendationDetail(recId),
    [recId],
  );
  const [confirmation, setConfirmation] = useState<ExecutionConfirmation | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmationError, setConfirmationError] = useState("");

  async function runExecutionConfirmation() {
    setConfirming(true);
    setConfirmationError("");
    try {
      setConfirmation(await api.confirmRecommendationExecution(recId));
    } catch (e) {
      setConfirmationError((e as Error).message || "次日执行确认失败");
    } finally {
      setConfirming(false);
    }
  }

  if (loading) return <PageSkeleton title="股票分析记录" hint="加载决策详情与收益复盘" rowCount={10} />;
  if (error) return <ErrorPage title="分析记录加载失败" message={error} onRetry={onBack} />;
  if (!data) return null;

  const { outcome, timeline, score_breakdown, review_note, feature_snapshot } = data;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <button className="btn btn--ghost" onClick={onBack} style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={14} />
        返回运行详情
      </button>

      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">推荐股票分析</div>
            <h3 className="panel__title" style={{ fontFamily: "var(--font-mono)" }}>
              {data.code}
              <span style={{ fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif", marginLeft: "var(--space-3)", color: "var(--ink)" }}>
                {data.name}
              </span>
            </h3>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
            {data.status && <StatusBadge status={data.status} />}
            <button className="btn btn--primary" onClick={runExecutionConfirmation} disabled={confirming}>
              {confirming ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
              {confirming ? "策略确认中…" : confirmation ? "重新确认" : "次日执行确认"}
            </button>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "var(--space-3)" }}>
          <Metric label="推荐日" value={data.trade_date} mono />
          <Metric
            label="分数"
            value={data.final_score.toFixed(1)}
            mono
            badge={<TicketScoreBadge finalScore={data.final_score} scoreLineMeta={data.score_line_meta} />}
          />
          <Metric label="共识分" value={data.consensus_score.toFixed(1)} mono />
          <Metric label="推荐价" value={fmtPrice(data.recommend_price)} mono />
          <Metric label="策略/买点" value={`${bucketLabel(data.bucket)} · ${data.setup_type || "—"}`} />
          <Metric label="风险等级" value={data.risk_tags?.[0] || "—"} />
        </div>
      </section>

      {confirmationError && (
        <div style={{ color: "var(--market-down)", fontSize: "var(--text-sm)" }}>{confirmationError}</div>
      )}
      {confirmation && <ExecutionConfirmationSection result={confirmation} />}

      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">决策分析</div>
            <h3 className="panel__title">为什么推荐它</h3>
          </div>
        </div>
        <RecommendationDetailCard item={data} />
      </section>

      {feature_snapshot && Object.keys(feature_snapshot).length > 0 && (
        <FeatureSnapshotSection snapshot={feature_snapshot} />
      )}
      {(outcome || (timeline && timeline.length > 0)) && (
        <OutcomeSection outcome={outcome} timeline={timeline} />
      )}
      {score_breakdown && Object.keys(score_breakdown).length > 0 && (
        <ScoreBreakdownSection breakdown={score_breakdown} />
      )}
      {review_note && (review_note.user_label || review_note.failure_reason || review_note.review_text) && (
        <ReviewNoteSection note={review_note} />
      )}
    </div>
  );
}

function decisionColor(decision: ExecutionDecision | undefined) {
  if (!decision) return "var(--muted)";
  if (["buy", "hold"].includes(decision.code)) return "var(--market-up)";
  if (["cancel", "exit", "expired"].includes(decision.code)) return "var(--market-down)";
  return "var(--accent)";
}

function DecisionCard({ title, decision }: { title: string; decision?: ExecutionDecision }) {
  const color = decisionColor(decision);
  return (
    <div style={{ padding: "var(--space-3)", border: "1px solid var(--line)", borderRadius: "var(--radius-md)", background: "var(--surface-soft)" }}>
      <div style={{ color: "var(--muted)", fontSize: "var(--text-xs)", marginBottom: 4 }}>{title}</div>
      <div style={{ color, fontWeight: 700, fontSize: "var(--text-lg)", marginBottom: 4 }}>{decision?.label || "—"}</div>
      <div style={{ color: "var(--ink-secondary)", fontSize: "var(--text-sm)", lineHeight: 1.55 }}>{decision?.reason || "暂无结论"}</div>
    </div>
  );
}

function ExecutionConfirmationSection({ result }: { result: ExecutionConfirmation }) {
  const signal = result.strategy_signal;
  const review = result.agent_review;
  const market = result.market;
  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">当前策略复核</div>
          <h3 className="panel__title">次日执行确认</h3>
        </div>
        <span style={{ color: result.fresh ? "var(--market-up)" : "var(--market-down)", fontSize: "var(--text-sm)" }}>
          {result.fresh
            ? `数据截至 ${result.data_as_of}${result.quote_time || result.snapshot_generated_at ? ` · ${formatTime(result.quote_time || result.snapshot_generated_at)}` : ""}`
            : `数据未就绪 · ${result.data_as_of || "—"}`}
        </span>
      </div>

      {result.message ? (
        <div style={{ color: "var(--market-down)", fontSize: "var(--text-sm)" }}>{result.message}</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "var(--space-3)" }}>
            <DecisionCard title="如果还未买入" decision={result.entry_decision} />
            <DecisionCard title="如果已经持仓" decision={result.holding_decision} />
          </div>

          <div style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", marginTop: "var(--space-3)", fontSize: "var(--text-sm)", color: "var(--ink-secondary)" }}>
            <span>策略 <b>{result.strategy_name || result.strategy_id || "—"}</b></span>
            <span>现价 <b className="text-mono">{result.current_price != null ? result.current_price.toFixed(2) : "—"}</b></span>
            <span>阶段 <b>{signal?.phase || "—"}</b></span>
            <span>规则分 <b className="text-mono">{signal?.score != null ? signal.score.toFixed(1) : "—"}</b></span>
            <span>当前买点 <b>{signal?.setup_match || "未命中"}</b></span>
            <span>市场 <b>{market?.regime || "—"}</b></span>
          </div>

          <div style={{ marginTop: "var(--space-3)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)", fontSize: "var(--text-sm)", lineHeight: 1.6 }}>
            <div><span style={{ color: "var(--muted)" }}>原入场条件 · </span>{result.original?.entry_rule || "—"}</div>
            <div><span style={{ color: "var(--muted)" }}>原失效条件 · </span>{result.original?.invalid_rule || "—"}</div>
            <div><span style={{ color: "var(--muted)" }}>当前主线 · </span>{market?.main_industries?.join(" / ") || "—"}</div>
            {review?.summary && <div><span style={{ color: "var(--muted)" }}>策略审查 · </span>{review.summary}</div>}
            {review?.status === "fallback" && (
              <div style={{ color: "var(--accent)" }}>Agent 暂不可用，本次结论仅使用确定性规则。</div>
            )}
          </div>

          {signal?.risk_flags && signal.risk_flags.length > 0 && (
            <ul style={{ margin: "var(--space-3) 0 0 var(--space-4)", padding: 0, color: "var(--market-down)", fontSize: "var(--text-sm)" }}>
              {signal.risk_flags.slice(0, 4).map((risk, index) => <li key={index}>{risk}</li>)}
            </ul>
          )}
          <div style={{ marginTop: "var(--space-3)", color: "var(--muted)", fontSize: "var(--text-xs)" }}>
            仅使用数据截至时点已经可见的信息；原推荐超过首个交易日后，入场信号自动过期。买入当日仍受 T+1 限制。
          </div>
        </>
      )}
    </section>
  );
}

function FeatureSnapshotSection({ snapshot }: { snapshot: Record<string, unknown> }) {
  const num = (v: unknown) => (typeof v === "number" && !Number.isNaN(v) ? v.toFixed(2) : "—");
  const rows: Array<[string, string]> = [
    ["现价", num(snapshot.price)],
    ["MA5 / MA10 / MA20", `${num(snapshot.ma5)} / ${num(snapshot.ma10)} / ${num(snapshot.ma20)}`],
    ["趋势状态", String(snapshot.trend_status ?? "—")],
    ["MACD", `${String(snapshot.macd_status ?? "—")} (${num(snapshot.macd_dif)}/${num(snapshot.macd_dea)})`],
    ["RSI(12)", num(snapshot.rsi_12)],
    ["量比(5日)", num(snapshot.volume_ratio_5d)],
    ["支撑位", Array.isArray(snapshot.support_levels) ? (snapshot.support_levels as number[]).map((x) => x.toFixed(2)).join(" / ") || "—" : "—"],
    ["阻力位", Array.isArray(snapshot.resistance_levels) ? (snapshot.resistance_levels as number[]).map((x) => x.toFixed(2)).join(" / ") || "—" : "—"],
  ];
  return (
    <section className="panel">
      <div className="panel__header">
        <div><div className="panel__kicker">技术快照</div><h3 className="panel__title">推荐日技术面</h3></div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-3)" }}>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{label}</span>
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function OutcomeSection({
  outcome,
  timeline,
}: {
  outcome: RecommendationOutcome | null;
  timeline: Array<{ label: string; trade_date: string; close_return: number; max_return: number; max_drawdown: number }>;
}) {
  return (
    <section className="panel">
      <div className="panel__header">
        <div><div className="panel__kicker">收益与复盘</div><h3 className="panel__title">分析日 → 今天</h3></div>
        {outcome?.outcome_label && (
          (() => {
            const tone = outcomeTone(outcome.outcome_label);
            const toneColor =
              tone === "positive" ? "var(--market-up)"
                : tone === "danger" ? "var(--market-down)"
                : tone === "warning" ? "var(--market-gold, #b8860b)"
                : "var(--muted)";
            return (
              <span style={{
                padding: "2px 8px", borderRadius: 4, fontSize: "var(--text-sm)",
                color: toneColor,
                background: `color-mix(in srgb, ${toneColor} 12%, transparent)`,
              }}>{outcomeLabel(outcome.outcome_label)}</span>
            );
          })()
        )}
      </div>
      {outcome && (
        <div style={{ display: "flex", gap: "var(--space-5)", flexWrap: "wrap", padding: "var(--space-3) 0", borderBottom: "1px solid var(--border)" }}>
          <ReturnStat label="收盘收益" value={outcome.close_return} />
          <ReturnStat label="最大收益" value={outcome.max_return} />
          <ReturnStat label="最大回撤" value={outcome.max_drawdown} />
          {outcome.holding_days != null && <ReturnStat label="持仓天数" value={outcome.holding_days} />}
          {outcome.exit_reason && (
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>退出原因</span>
              <span style={{ fontSize: "var(--text-base)", color: "var(--text)" }}>{exitReasonLabel(outcome.exit_reason, "—", outcome.executed_return)}</span>
            </div>
          )}
        </div>
      )}
      {outcome?.entry_date && (
        <div style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", alignItems: "center", padding: "var(--space-2) 0", fontSize: "var(--text-sm)", color: "var(--muted)" }}>
          <span>入场基准：</span>
          <span style={{ fontFamily: "var(--font-mono)" }}>{outcome.entry_date}</span>
          {outcome.entry_price != null && (
            <span style={{ fontFamily: "var(--font-mono)" }}>开 {outcome.entry_price.toFixed(2)}</span>
          )}
          {outcome.exit_date && (
            <>
              <span>→</span>
              <span style={{ fontFamily: "var(--font-mono)" }}>{outcome.exit_date} 离场</span>
            </>
          )}
          <span style={{ color: "var(--line-strong)" }}>·</span>
          <span>T+1 交收口径（入场日不触发离场，含跳空止损）</span>
        </div>
      )}
      {timeline && timeline.length > 0 && (
        <div style={{ overflowX: "auto", marginTop: "var(--space-3)" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>阶段</th>
                <th style={thStyle}>交易日</th>
                <th style={{ ...thStyle, textAlign: "right" }}>收盘收益</th>
                <th style={{ ...thStyle, textAlign: "right" }}>最大收益</th>
                <th style={{ ...thStyle, textAlign: "right" }}>最大回撤</th>
              </tr>
            </thead>
            <tbody>
              {timeline.map((t, i) => (
                <tr key={i} style={rowStyle}>
                  <td style={tdStyle}>{t.label}</td>
                  <td style={{ ...tdStyle, fontFamily: "var(--font-mono)", color: "var(--muted)" }}>{t.trade_date}</td>
                  <ReturnCell value={t.close_return} />
                  <ReturnCell value={t.max_return} />
                  <ReturnCell value={t.max_drawdown} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ScoreBreakdownSection({ breakdown }: { breakdown: Record<string, unknown> }) {
  const weighted = breakdown.weighted_scores as Record<string, number> | undefined;
  const raw = breakdown.raw_scores as Record<string, number> | undefined;
  const entries = weighted ?? raw ?? {};
  const keys = Object.keys(entries);
  return (
    <section className="panel">
      <div className="panel__header">
        <div><div className="panel__kicker">评分构成</div><h3 className="panel__title">分数怎么来的</h3></div>
      </div>
      {keys.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {keys.map((k) => {
            const v = entries[k];
            const max = Math.max(1, ...Object.values(entries).map((x) => (typeof x === "number" ? x : 0)));
            const pct = typeof v === "number" ? Math.max(0, Math.min(100, (v / max) * 100)) : 0;
            return (
              <div key={k}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--text-sm)", marginBottom: 2 }}>
                  <span style={{ color: "var(--text-muted)" }}>{k}</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{typeof v === "number" ? v.toFixed(1) : "—"}</span>
                </div>
                <div style={{ height: 6, background: "var(--surface-soft)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent)", borderRadius: 4 }} />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>无评分明细</div>
      )}
      <div style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", marginTop: "var(--space-3)", fontSize: "var(--text-sm)" }}>
        {"consensus_boost" in breakdown && <span>共识加成 <b className="text-mono">{numOr(breakdown.consensus_boost)}</b></span>}
        {"quality_cap" in breakdown && <span>质量上限 <b className="text-mono">{numOr(breakdown.quality_cap)}</b></span>}
        {"final_score" in breakdown && <span>最终分 <b className="text-mono">{numOr(breakdown.final_score)}</b></span>}
      </div>
    </section>
  );
}

function ReviewNoteSection({ note }: { note: { user_label?: string | null; failure_reason?: string | null; review_text?: string | null; updated_at?: string | null } }) {
  return (
    <section className="panel">
      <div className="panel__header">
        <div><div className="panel__kicker">复盘笔记</div><h3 className="panel__title">人工复盘</h3></div>
        {note.updated_at && <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{formatTime(note.updated_at)}</span>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", fontSize: "var(--text-sm)" }}>
        {note.user_label && <div><span style={{ color: "var(--text-muted)" }}>结论 · </span><span style={{ color: "var(--ink-secondary)" }}>{note.user_label}</span></div>}
        {note.failure_reason && <div><span style={{ color: "var(--text-muted)" }}>失败原因 · </span><span style={{ color: "var(--ink-secondary)" }}>{note.failure_reason}</span></div>}
        {note.review_text && <p style={{ margin: 0, color: "var(--ink-secondary)", lineHeight: 1.6 }}>{note.review_text}</p>}
      </div>
    </section>
  );
}

const numOr = (v: unknown) =>
  typeof v === "number" && !Number.isNaN(v) ? v.toFixed(1) : "—";
