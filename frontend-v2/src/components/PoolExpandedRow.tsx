import { ClipboardCopy, History, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { CockpitRecommendation, OutcomeSummary } from "../types";
import { TradeMetric } from "./RecommendationDetailCard";
import { StockTicker } from "./StockTicker";

/**
 * 候选池展开行 —— 从 PoolPage 抽离的结构化子组件。
 *
 * 含 LLM 可信度指示器（确定 / 不确定 / 历史验证率），
 * 交易参数（推荐价/止损/止盈/理想买点）、买点原因、市场阶段、评分分解、规则、风险标签。
 *
 * TradeMetric 复用 RecommendationDetailCard 的实现，消除重复。
 */
type RiskLevel = CockpitRecommendation["risk_level"];

export function PoolExpandedRow({
  item,
  outcomeSummary,
  onReplay,
  onViewHistory,
}: {
  item: CockpitRecommendation;
  outcomeSummary?: Record<string, OutcomeSummary>;
  onReplay: () => void;
  onViewHistory: () => void;
}) {
  const confidence = deriveConfidence(item);
  // 真实历史表现：优先用后端聚合的已结算胜率/均收益，替换 ConfidenceMeter 的 score_gap/20 启发式
  const os = outcomeSummary?.[item.code];
  const hasHistory = !!os && os.settled > 0;
  const effectiveValidated = hasHistory ? os.win_rate / 100 : confidence.validatedRatio;
  const hasTradeParams =
    item.recommend_price != null || item.stop_loss != null || item.take_profit != null;
  return (
    <div
      style={{
        padding: "var(--space-4)",
        borderTop: "1px solid var(--line)",
        background: "var(--surface-soft)",
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: "var(--space-4)",
      }}
    >
      {/* 身份条：展开后保留股票代码 + 名称，避免失去上下文 */}
      <div
        style={{
          gridColumn: "1 / -1",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          flexWrap: "wrap",
          marginBottom: "var(--space-1)",
        }}
      >
        <StockTicker code={item.code} size="md" />
        <span style={{ fontWeight: 700, color: "var(--ink)", fontSize: "var(--text-md)" }}>{item.name}</span>
        {item.industry && <span className="tag">{item.industry}</span>}
      </div>

      {/* 左：交易参数 + 买点原因 / 风险提示 */}
      <div>
        {hasTradeParams && (
          <div style={{ marginBottom: "var(--space-3)" }}>
            <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>交易参数</div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: "var(--space-2)",
              }}
            >
              <TradeMetric label="推荐价" value={item.recommend_price} />
              <TradeMetric label="止损" value={item.stop_loss} tone="down" />
              <TradeMetric label="止盈" value={item.take_profit} tone="up" />
            </div>
            {(item.ideal_buy != null || item.secondary_buy != null) && (
              <div style={{ marginTop: "var(--space-2)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                理想买点 {item.ideal_buy != null ? item.ideal_buy.toFixed(2) : "—"}
                {item.secondary_buy != null ? ` · 次选 ${item.secondary_buy.toFixed(2)}` : ""}
              </div>
            )}
          </div>
        )}
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>买点原因</div>
        <p style={{ margin: 0, marginBottom: "var(--space-3)", color: "var(--ink-secondary)", fontSize: "var(--text-sm)", lineHeight: 1.6 }}>
          {item.rationale || "—"}
        </p>
        {(() => {
          const breadth = (item.market_context?.breadth ?? {}) as Record<string, unknown>;
          const regime = breadth.regime as string | undefined;
          const risk = breadth.risk as string | undefined;
          const hotTheme = breadth.hot_theme as string | undefined;
          const industries = item.market_context?.main_industries ?? [];
          if (!regime && !risk && !hotTheme && industries.length === 0) return null;
          return (
            <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
              {regime && (
                <div style={{ marginBottom: "var(--space-1)" }}>
                  <span style={{ color: "var(--text-muted)" }}>市场阶段 · </span>
                  <span style={{ color: "var(--ink-secondary)" }}>{regime}</span>
                  {risk && <span style={{ color: "var(--text-muted)" }}>（风险 {risk}）</span>}
                </div>
              )}
              {hotTheme && (
                <div style={{ marginBottom: "var(--space-1)" }}>
                  <span style={{ color: "var(--text-muted)" }}>热门题材 · </span>
                  <span style={{ color: "var(--ink-secondary)" }}>{hotTheme}</span>
                </div>
              )}
              {industries.length > 0 && (
                <div>
                  <span style={{ color: "var(--text-muted)" }}>主线 · </span>
                  <span style={{ color: "var(--ink-secondary)" }}>{industries.join(" / ")}</span>
                </div>
              )}
            </div>
          );
        })()}
        {item.skill_breakdown && item.skill_breakdown.length > 0 && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
            <div className="panel__kicker" style={{ marginBottom: "var(--space-1)" }}>评分分解</div>
            {item.skill_breakdown.map((sk, i) => (
              <div key={i} style={{ marginBottom: "var(--space-1)", color: "var(--ink-secondary)" }}>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent-strong)" }}>
                  {sk.score.toFixed(0)}
                </span>
                <span style={{ color: "var(--text-muted)" }}> · {sk.strategy_name}</span>
                {sk.phase && <span style={{ color: "var(--text-muted)" }}> · {sk.phase}</span>}
                {sk.hard_veto && (
                  <span style={{ color: "var(--market-up)" }}> · 否决（{sk.veto_reason || "硬风控"}）</span>
                )}
                {sk.evidence.length > 0 && (
                  <div style={{ color: "var(--text-muted)", marginTop: 2 }}>
                    {sk.evidence.slice(0, 4).join(" / ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        {(item.entry_rule || item.invalid_rule) && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
            {item.entry_rule && (
              <div style={{ marginBottom: "var(--space-1)" }}>
                <span style={{ color: "var(--text-muted)" }}>入场规则 · </span>
                <span style={{ color: "var(--ink-secondary)" }}>{item.entry_rule}</span>
              </div>
            )}
            {item.invalid_rule && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>失效规则 · </span>
                <span style={{ color: "var(--ink-secondary)" }}>{item.invalid_rule}</span>
              </div>
            )}
          </div>
        )}
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>风险标签</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-1)" }}>
          {item.risk_tags.length === 0 ? (
            <span className="text-muted" style={{ fontSize: "var(--text-sm)" }}>无风险标签</span>
          ) : (
            item.risk_tags.map((t) => (
              <span key={t} className="tag tag--warning">
                {t}
              </span>
            ))
          )}
        </div>
        {/* 历史表现：真实已结算胜率/均收益，补全北极星「跑出来结果如何」维度 */}
        {hasHistory ? (
          <div style={{ marginTop: "var(--space-3)" }}>
            <div className="panel__kicker" style={{ marginBottom: "var(--space-1)" }}>
              历史表现 · 已结算 {os!.settled} 次
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-3)", flexWrap: "wrap" }}>
              <span
                className="text-mono"
                style={{
                  fontWeight: 700,
                  fontSize: "var(--text-lg)",
                  color: os!.win_rate >= 50 ? "var(--market-up)" : "var(--market-down)",
                }}
              >
                {os!.win_rate.toFixed(0)}%
              </span>
              <span style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>
                胜率（{os!.wins}/{os!.settled}）
              </span>
              <span
                style={{
                  fontSize: "var(--text-sm)",
                  fontWeight: 600,
                  color: os!.avg_return >= 0 ? "var(--market-up)" : "var(--market-down)",
                }}
              >
                {os!.avg_return >= 0 ? "+" : ""}
                {os!.avg_return.toFixed(2)}% 均收益
              </span>
            </div>
          </div>
        ) : (
          <div style={{ marginTop: "var(--space-3)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
            该股暂无已结算的历史推荐记录
          </div>
        )}
      </div>

      {/* 右：LLM 可信度 + 操作 */}
      <div>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>LLM 解释可信度 · 仅供参考，不影响出票</div>
        <ConfidenceMeter
          confidence={confidence}
          validatedRatio={effectiveValidated}
          validatedFrom={hasHistory ? "real" : "estimate"}
        />
        <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-4)" }}>
          <button className="btn btn--primary" onClick={onReplay}>
            <History size={14} />
            进入复盘
          </button>
          <button className="btn" onClick={() => navigator.clipboard?.writeText(item.rationale)}>
            <ClipboardCopy size={14} />
            复制解释
          </button>
          <button className="btn btn--ghost" onClick={onViewHistory}>
            查看历史
          </button>
        </div>
      </div>
    </div>
  );
}

/** 候选解释的可信度指示器 —— 见报告 §7.4 LLM 解释可信度 */
type Confidence = {
  certain: number; // 确定信号数
  uncertain: number; // 不确定信号数
  validatedRatio: number; // 历史验证率 0~1
  level: "high" | "medium" | "low";
};

function deriveConfidence(item: CockpitRecommendation): Confidence {
  // 简化派生：基于 risk_tags / info_tags / score_gap 的启发式
  const certain = item.info_tags.length;
  const uncertain = item.risk_tags.length;
  const validatedRatio = item.score_gap != null ? Math.max(0, Math.min(1, item.score_gap / 20)) : 0.5;
  const level = certain >= 3 && uncertain <= 1 ? "high" : uncertain >= 3 ? "low" : "medium";
  return { certain, uncertain, validatedRatio, level };
}

function ConfidenceMeter({
  confidence,
  validatedRatio,
  validatedFrom = "estimate",
}: {
  confidence: Confidence;
  validatedRatio?: number;
  validatedFrom?: "real" | "estimate";
}) {
  const { certain, uncertain, level } = confidence;
  const ratio = validatedRatio ?? confidence.validatedRatio;
  const Icon = level === "high" ? ShieldCheck : level === "low" ? ShieldAlert : ShieldQuestion;
  const color = level === "high" ? "var(--market-down)" : level === "low" ? "var(--market-up)" : "var(--warning)";
  const label = level === "high" ? "高可信" : level === "low" ? "低可信" : "中等";

  return (
    <div
      style={{
        padding: "var(--space-3)",
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-md)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <Icon size={16} style={{ color }} />
        <strong style={{ color }}>{label}</strong>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-2)", fontSize: "var(--text-xs)" }}>
        <div>
          <div className="text-muted">确定信号</div>
          <div className="text-mono" style={{ color: "var(--market-down)", fontWeight: 600 }}>{certain} 项</div>
        </div>
        <div>
          <div className="text-muted">不确定</div>
          <div className="text-mono" style={{ color: "var(--warning)", fontWeight: 600 }}>{uncertain} 项</div>
        </div>
        <div>
          <div className="text-muted">
            历史验证{validatedFrom === "real" ? "·真" : "·估"}
          </div>
          <div className="text-mono" style={{ color: "var(--accent-strong)", fontWeight: 600 }}>
            {(ratio * 100).toFixed(0)}%
          </div>
        </div>
      </div>
      <div
        style={{
          marginTop: "var(--space-2)",
          height: 4,
          background: "var(--surface-soft)",
          borderRadius: "var(--radius-full)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${ratio * 100}%`,
            background: "var(--accent)",
            transition: "width var(--motion-normal)",
          }}
        />
      </div>
    </div>
  );
}

// RiskLevel 重新导出以保持类型兼容（PoolPage 仍使用 RiskTag）
export type { RiskLevel };
