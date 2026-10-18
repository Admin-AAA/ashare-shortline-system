import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { AgentLog, RecommendationCard, RecommendationDetail, SkillSignal, StrategyPerfSummary } from "../types";
import { ruleKeyLevelLabels, rulesMentionKeyLevel } from "../utils/ruleKeyLevels";
import { formatApiDateTime } from "../utils/display";
import { isPlaceholderEntryRule } from "../utils/executionConfirmation";
import { MultiStrategyBadge } from "./MultiStrategyBadge";

/**
 * 推荐详情卡片 —— 展示完整决策信息（交易参数/买点原因/市场阶段/评分分解/规则/风险）。
 * 从 PoolPage ExpandedRow 提取，供 PoolPage、HistoryPage 等多处复用。
 *
 * 使用方式：
 *   <RecommendationDetailCard item={recommendation} />
 *
 * 或配合行内展开（HistoryPage 模式）：
 *   <InlineDetailLoader recommendationId={id} open={open} onToggle={toggle} />
 */

/* ---------- 行内展开加载器 ---------- */

/** 点击展开、按需 fetch 推荐详情。适用于 HistoryPage 等需要行内展开的场景。 */
export function InlineDetailLoader({
  recommendationId,
  open,
  onToggle,
}: {
  recommendationId: string;
  open: boolean;
  onToggle: () => void;
}) {
  const [detail, setDetail] = useState<RecommendationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleToggle = async () => {
    if (open) {
      onToggle();
      return;
    }
    onToggle();
    if (detail) return; // 已加载
    setLoading(true);
    setError("");
    try {
      const res = await api.recommendationDetail(recommendationId);
      setDetail(res);
    } catch (e) {
      setError((e as Error).message || "加载失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button
        onClick={(e) => {
          e.stopPropagation();
          handleToggle();
        }}
        style={toggleBtnStyle}
        title={open ? "收起详情" : "展开详情"}
      >
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && (
        <tr>
          <td colSpan={10} style={{ padding: 0 }}>
            {loading ? (
              <div style={loadingStyle}>
                <Loader2 size={16} className="spin" /> 加载决策详情…
              </div>
            ) : error ? (
              <div style={errorStyle}>{error}</div>
            ) : detail ? (
              <RecommendationDetailCard item={detail} />
            ) : null}
          </td>
        </tr>
      )}
    </>
  );
}

/* ---------- 详情卡片主体 ---------- */

type DetailItem = Pick<
  RecommendationCard,
  | "recommend_price"
  | "stop_loss"
  | "take_profit"
  | "rationale"
  | "entry_rule"
  | "invalid_rule"
  | "key_levels"
  | "feature_snapshot"
  | "risk_tags"
  | "info_tags"
  | "skill_breakdown"
  | "market_context"
  | "setup_type"
> & {
  trade_date?: string | null;
  recommended_at?: string | null;
  ideal_buy?: number | null;
  secondary_buy?: number | null;
  agent_logs?: AgentLog[];
  strategy_perf?: StrategyPerfSummary | null;
  strategy_perf_status?: "ready" | "pending_backtest" | "unavailable";
};

/** 完整决策详情卡片 —— 交易参数 + 买点原因 + 市场阶段 + 评分分解 + 规则 + 风险标签 */
export function RecommendationDetailCard({ item }: { item: DetailItem }) {
  const hasTradeParams =
    item.recommend_price != null || item.stop_loss != null || item.take_profit != null;
  const keyLevels = { ...(item.feature_snapshot ?? {}), ...(item.key_levels ?? {}) };
  const keyLevelLabels = ruleKeyLevelLabels(item.entry_rule, item.invalid_rule, keyLevels);
  const hasUnresolvedKeyLevel = keyLevelLabels.length === 0
    && rulesMentionKeyLevel(item.entry_rule, item.invalid_rule);
  const missingEntryRule = isPlaceholderEntryRule(item.entry_rule);

  return (
    <div style={cardContainerStyle}>
      <div>
        {(item.trade_date || item.recommended_at) && (
          <div
            style={{
              marginBottom: "var(--space-3)",
              fontSize: "var(--text-xs)",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
            }}
          >
            <span className="panel__kicker" style={{ marginBottom: 0 }}>推荐时间</span>
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink-secondary)" }}>
              {item.recommended_at
                ? formatApiDateTime(item.recommended_at)
                : item.trade_date}
            </span>
            {item.recommended_at && (
              <span style={{ opacity: 0.75 }}>北京时间 · 盘后推荐次日入场</span>
            )}
          </div>
        )}
        {hasTradeParams && (
          <div style={{ marginBottom: "var(--space-3)" }}>
            <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>交易参数</div>
            <div style={tradeGridStyle}>
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

        {item.setup_type && (
          <div
            style={{
              marginBottom: "var(--space-3)",
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
            }}
          >
            <span className="panel__kicker" style={{ marginBottom: 0 }}>
              买点形态
            </span>
            <span
              className="tag"
              style={{
                color: "var(--accent-strong)",
                border: "1px solid var(--accent-line, var(--line))",
              }}
            >
              {item.setup_type}
            </span>
            <MultiStrategyBadge skillBreakdown={item.skill_breakdown} />
          </div>
        )}

        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>买点原因</div>
        <p style={rationaleStyle}>{item.rationale || "—"}</p>

        <MarketContextSection marketContext={item.market_context} />
        <SkillBreakdownSection skillBreakdown={item.skill_breakdown} />
        <AgentReviewSection agentLogs={item.agent_logs} />
        <StrategyPerfSection
          strategyPerf={item.strategy_perf}
          status={item.strategy_perf_status}
        />

        {(keyLevelLabels.length > 0 || hasUnresolvedKeyLevel) && (
          <div style={{ marginBottom: "var(--space-3)" }}>
            <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>规则关键价位</div>
            {keyLevelLabels.length > 0 ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
                {keyLevelLabels.map((label) => (
                  <span key={label} className="tag" style={{ color: "var(--accent-strong)", fontFamily: "var(--font-mono)" }}>
                    {label}
                  </span>
                ))}
              </div>
            ) : (
              <span style={{ color: "var(--market-down)", fontSize: "var(--text-xs)" }}>
                这笔旧记录没有保存规则引用的支撑或平台价位
              </span>
            )}
          </div>
        )}

        {(missingEntryRule || item.entry_rule || item.invalid_rule) && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
            {missingEntryRule ? (
              <div style={{ marginBottom: "var(--space-1)" }}>
                <span style={{ color: "var(--text-muted)" }}>入场规则 · </span>
                <span style={{ color: "var(--text-muted)", fontStyle: "italic" }}>原推荐未保存明确买点</span>
              </div>
            ) : item.entry_rule ? (
              <div style={{ marginBottom: "var(--space-1)" }}>
                <span style={{ color: "var(--text-muted)" }}>入场规则 · </span>
                <span style={{ color: "var(--ink-secondary)" }}>{item.entry_rule}</span>
              </div>
            ) : null}
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
      </div>
    </div>
  );
}

/* ---------- 子区块 ---------- */

function MarketContextSection({ marketContext }: { marketContext: DetailItem["market_context"] }) {
  const breadth = (marketContext?.breadth ?? {}) as Record<string, unknown>;
  const regime = breadth.regime as string | undefined;
  const risk = breadth.risk as string | undefined;
  const hotTheme = breadth.hot_theme as string | undefined;
  const industries = marketContext?.main_industries ?? [];

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
}

function SkillBreakdownSection({ skillBreakdown }: { skillBreakdown: SkillSignal[] }) {
  if (!skillBreakdown || skillBreakdown.length === 0) return null;

  return (
    <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
      <div className="panel__kicker" style={{ marginBottom: "var(--space-1)" }}>评分分解</div>
      {skillBreakdown.map((sk, i) => (
        <div key={i} style={{ marginBottom: "var(--space-1)", color: "var(--ink-secondary)" }}>
          <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent-strong)" }}>
            {sk.score.toFixed(0)}
          </span>
          <span style={{ color: "var(--text-muted)" }}> · {sk.strategy_name}</span>
          {sk.phase && <span style={{ color: "var(--text-muted)" }}> · {sk.phase}</span>}
          {sk.setup_match && <span style={{ color: "var(--text)" }}> · 买点: {sk.setup_match}</span>}
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
  );
}

/** LLM Skill Agent 审查区块 —— 展示 stance / score_delta / summary / action / risks
 * 决策链关键一环：规则层之上 LLM 的独立判断，让用户看到"AI 怎么看这只票"。 */
function AgentReviewSection({ agentLogs }: { agentLogs?: AgentLog[] }) {
  const review = agentLogs?.[0]?.output as Record<string, unknown> | undefined;
  if (!review) return null;

  const stance = review.stance as string | undefined;
  const scoreDelta = review.score_delta as number | undefined;
  const summary = review.summary as string | undefined;
  const action = review.action as string | undefined;
  const positionRange = review.position_range as string | undefined;
  const entryStyle = review.entry_style as string | undefined;
  const risks = (review.risks as string[] | undefined) ?? [];
  const invalidation = (review.invalidation_conditions as string[] | undefined) ?? [];

  // stance → 颜色映射（A 股红涨绿跌语境：support 偏暖色，oppose/veto 偏冷色）
  const stanceColor =
    stance === "strong_support"
      ? "var(--market-up)"
      : stance === "support"
        ? "var(--market-up)"
        : stance === "veto" || stance === "oppose"
          ? "var(--market-down)"
          : "var(--text-muted)";

  return (
    <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
      <div className="panel__kicker" style={{ marginBottom: "var(--space-1)" }}>LLM 提示 · 仅供参考，不影响出票</div>
      <div style={{ marginBottom: "var(--space-1)", color: "var(--ink-secondary)" }}>
        {stance && (
          <span style={{ fontWeight: 600, color: stanceColor }}>{stanceLabel(stance)}</span>
        )}
        {scoreDelta != null && (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              color: "var(--muted)",
              marginLeft: "var(--space-2)",
            }}
          >
            建议 {scoreDelta >= 0 ? "+" : ""}{scoreDelta.toFixed(1)}（未计入）
          </span>
        )}
        {positionRange && (
          <span style={{ color: "var(--text-muted)", marginLeft: "var(--space-2)" }}>· 仓位 {positionRange}</span>
        )}
      </div>
      {summary && (
        <p style={{ margin: "0 0 var(--space-1)", color: "var(--ink-secondary)", fontSize: "var(--text-sm)", lineHeight: 1.6 }}>
          {summary}
        </p>
      )}
      {action && (
        <div style={{ marginBottom: "var(--space-1)", color: "var(--text-muted)" }}>
          建议 · <span style={{ color: "var(--ink-secondary)" }}>{action}</span>
          {entryStyle && <span style={{ color: "var(--text-muted)" }}>（{entryStyle}）</span>}
        </div>
      )}
      {risks.length > 0 && (
        <div style={{ color: "var(--text-muted)", marginBottom: risks.length ? "var(--space-1)" : 0 }}>
          <span>风险 · </span>
          <span style={{ color: "var(--ink-secondary)" }}>{risks.slice(0, 4).join(" / ")}</span>
        </div>
      )}
      {invalidation.length > 0 && (
        <div style={{ color: "var(--text-muted)" }}>
          <span>失效 · </span>
          <span style={{ color: "var(--ink-secondary)" }}>{invalidation.slice(0, 3).join(" / ")}</span>
        </div>
      )}
    </div>
  );
}

/** 当前版本半年 canonical 回测画像；不存在时明确 pending，禁止混用历史实盘。 */
function StrategyPerfSection({
  strategyPerf,
  status,
}: {
  strategyPerf?: StrategyPerfSummary | null;
  status?: "ready" | "pending_backtest" | "unavailable";
}) {
  if (!strategyPerf) {
    if (status !== "pending_backtest") return null;
    return (
      <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-1)" }}>当前策略 · 半年回测</div>
        当前版本画像待生成；未使用旧版本真实收益补位。
      </div>
    );
  }
  const {
    labeled_count,
    hit_rate,
    profit_loss_ratio,
    avg_return,
    total_return_pct,
    date_from,
    date_to,
    strategy_version,
  } = strategyPerf;
  // 样本 <10 不稳健，降级提示
  const thinSample = labeled_count < 10;
  const wrColor =
    hit_rate == null
      ? "var(--text-muted)"
      : hit_rate >= 60
        ? "var(--market-up)"
        : hit_rate >= 50
          ? "var(--ink-secondary)"
          : "var(--market-down)";

  return (
    <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
      <div className="panel__kicker" style={{ marginBottom: "var(--space-1)" }}>
        当前策略 · 半年 canonical 回测
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)", alignItems: "baseline" }}>
        {hit_rate != null && (
          <span>
            <span style={{ color: "var(--text-muted)" }}>胜率 </span>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: wrColor }}>
              {hit_rate.toFixed(1)}%
            </span>
            <span style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)" }}>
              {" "}({labeled_count}笔)
            </span>
          </span>
        )}
        {profit_loss_ratio != null && (
          <span>
            <span style={{ color: "var(--text-muted)" }}>盈亏比 </span>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--ink-secondary)" }}>
              {profit_loss_ratio.toFixed(2)}
            </span>
          </span>
        )}
        {avg_return != null && (
          <span>
            <span style={{ color: "var(--text-muted)" }}>均收 </span>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: avg_return >= 0 ? "var(--market-up)" : "var(--market-down)" }}>
              {avg_return >= 0 ? "+" : ""}{avg_return.toFixed(2)}%
            </span>
          </span>
        )}
        <span>
          <span style={{ color: "var(--text-muted)" }}>半年收益 </span>
          <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: total_return_pct >= 0 ? "var(--market-up)" : "var(--market-down)" }}>
            {total_return_pct >= 0 ? "+" : ""}{total_return_pct.toFixed(2)}%
          </span>
        </span>
      </div>
      <div style={{ color: "var(--text-muted)", marginTop: "var(--space-1)", fontFamily: "var(--font-mono)" }} title={strategy_version}>
        {date_from} ~ {date_to} · 版本 {strategy_version.slice(0, 18)}
      </div>
      {thinSample && (
        <div style={{ color: "var(--text-muted)", marginTop: "var(--space-1)" }}>
          样本 {labeled_count} 笔较少，数据仅供参考
        </div>
      )}
    </div>
  );
}

/** stance 原始值（strong_support/support/neutral_support/neutral/oppose/veto）→ 中文标签 */
function stanceLabel(stance: string): string {
  const map: Record<string, string> = {
    strong_support: "强支持",
    support: "支持",
    neutral_support: "中性偏多",
    neutral: "中性",
    oppose: "反对",
    veto: "否决",
  };
  return map[stance] ?? stance;
}

/** 交易参数指标块 —— 推荐价/止损/止盈等关键价格一屏可见 */
export function TradeMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  tone?: "up" | "down";
}) {
  const color =
    value == null
      ? "var(--text-muted)"
      : tone === "up"
        ? "var(--market-up)"
        : tone === "down"
          ? "var(--market-down)"
          : "var(--ink)";
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-2) var(--space-3)",
      }}
    >
      <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontFamily: "var(--font-mono, monospace)", fontSize: "var(--text-lg)", fontWeight: 600, color }}>
        {value != null ? value.toFixed(2) : "—"}
      </div>
    </div>
  );
}

/* ---------- 样式常量 ---------- */

const cardContainerStyle: React.CSSProperties = {
  padding: "var(--space-4)",
  borderTop: "1px solid var(--line)",
  background: "var(--surface-soft)",
};

const tradeGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(3, 1fr)",
  gap: "var(--space-2)",
};

const rationaleStyle: React.CSSProperties = {
  margin: 0,
  marginBottom: "var(--space-3)",
  color: "var(--ink-secondary)",
  fontSize: "var(--text-sm)",
  lineHeight: 1.6,
};

const toggleBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  cursor: "pointer",
  color: "var(--muted)",
  padding: "var(--space-1)",
  display: "inline-flex",
  alignItems: "center",
};

const loadingStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-2)",
  padding: "var(--space-3) var(--space-4)",
  color: "var(--muted)",
  fontSize: "var(--text-sm)",
  background: "var(--surface-soft)",
};

const errorStyle: React.CSSProperties = {
  padding: "var(--space-3) var(--space-4)",
  color: "var(--market-down)",
  fontSize: "var(--text-sm)",
  background: "var(--surface-soft)",
};
