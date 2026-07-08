/**
 * RecordsPage 股票分析详情 —— 从 RecordsPage.tsx 抽离（T10 重构）。
 * StockAnalysisDetailView + FeatureSnapshotSection + OutcomeSection +
 * ScoreBreakdownSection + ReviewNoteSection + numOr。
 */
import { ArrowLeft } from "lucide-react";
import { api } from "../api/client";
import type { RecommendationDetail, RecommendationOutcome } from "../types";
import { ErrorPage, PageSkeleton } from "./StateViews";
import { RecommendationDetailCard } from "./RecommendationDetailCard";
import { CollapsiblePanel } from "./CollapsiblePanel";
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
import { bucketLabel, exitReasonLabel } from "../utils/display";

export function StockAnalysisDetailView({ recId, onBack }: { recId: string; onBack: () => void }) {
  const { data, loading, error } = useApi<RecommendationDetail>(
    () => api.recommendationDetail(recId),
    [recId],
  );

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
          {data.status && <StatusBadge status={data.status} />}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "var(--space-3)" }}>
          <Metric label="推荐日" value={data.trade_date} mono />
          <Metric label="分数" value={data.final_score.toFixed(1)} mono />
          <Metric label="共识分" value={data.consensus_score.toFixed(1)} mono />
          <Metric label="推荐价" value={fmtPrice(data.recommend_price)} mono />
          <Metric label="策略/买点" value={`${bucketLabel(data.bucket)} · ${data.setup_type || "—"}`} />
          <Metric label="风险等级" value={data.risk_tags?.[0] || "—"} />
        </div>
      </section>

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
          <span style={{
            padding: "2px 8px", borderRadius: 4, fontSize: "var(--text-sm)",
            color: outcome.hit_target ? "var(--market-up)" : "var(--market-down)",
            background: `color-mix(in srgb, ${outcome.hit_target ? "var(--market-up)" : "var(--market-down)"} 12%, transparent)`,
          }}>{outcome.outcome_label}</span>
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
