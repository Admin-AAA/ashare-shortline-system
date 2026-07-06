import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { RecommendationCard, SkillSignal } from "../types";

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
  const [detail, setDetail] = useState<RecommendationCard | null>(null);
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
  | "risk_tags"
  | "info_tags"
  | "skill_breakdown"
  | "market_context"
> & {
  ideal_buy?: number | null;
  secondary_buy?: number | null;
};

/** 完整决策详情卡片 —— 交易参数 + 买点原因 + 市场阶段 + 评分分解 + 规则 + 风险标签 */
export function RecommendationDetailCard({ item }: { item: DetailItem }) {
  const hasTradeParams =
    item.recommend_price != null || item.stop_loss != null || item.take_profit != null;

  return (
    <div style={cardContainerStyle}>
      <div>
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

        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>买点原因</div>
        <p style={rationaleStyle}>{item.rationale || "—"}</p>

        <MarketContextSection marketContext={item.market_context} />
        <SkillBreakdownSection skillBreakdown={item.skill_breakdown} />

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
