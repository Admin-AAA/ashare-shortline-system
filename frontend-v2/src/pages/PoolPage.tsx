import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Search,
} from "lucide-react";
import { api } from "../api/client";
import type { CockpitRecommendation } from "../types";
import { EmptyState, ErrorPage } from "../components/StateViews";
import { PoolExpandedRow } from "../components/PoolExpandedRow";
import { StockTicker } from "../components/StockTicker";
import { useUrlRoute } from "../app/useUrlRoute";
import { useApi } from "../app/useApi";
import { queryParam } from "../app/nav";

/**
 * 候选池 —— 可展开行模式（替代原 master-detail）
 * 见 docs/UX-architecture-full-report.md §6.4
 *
 * 支持同时展开多行并排对比买点与风险。
 * 每张展开卡含 LLM 可信度指示器（确定 / 不确定 / 历史验证率）。
 */
type RiskLevel = CockpitRecommendation["risk_level"];

export function PoolPage() {
  const { navigate, navigateSection } = useUrlRoute();
  const runId = queryParam("run_id");

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<{ risk?: RiskLevel; q: string }>({ q: "" });
  // 若有 run_id 则加载该 run 的候选，否则加载最新候选
  // runRecommendations 返回 RecommendationCard[]（含 risk_level 等扩展字段，后端实际返回），
  // dashboard.overview 返回 CockpitRecommendation[]；统一按 CockpitRecommendation 消费
  const { data: items, loading, error } = useApi<CockpitRecommendation[]>(
    (signal) =>
      runId
        ? api.runRecommendations(runId).then((list) => list as unknown as CockpitRecommendation[])
        : api.dashboard.overview({ signal }).then((d) => d.recommendations),
    [runId],
  );

  const filtered = useMemo(() => {
    if (!items) return [];
    const q = filter.q.trim().toLowerCase();
    return items.filter((c) => {
      if (filter.risk && c.risk_level !== filter.risk) return false;
      if (q && !(`${c.code} ${c.name}`.toLowerCase().includes(q))) return false;
      return true;
    });
  }, [items, filter]);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 56 }} />
        ))}
      </div>
    );
  }
  if (error) return <ErrorPage title="候选加载失败" message={error} onRetry={() => window.location.reload()} />;
  if (!items || items.length === 0) {
    return (
      <EmptyState
        icon={<Search size={32} />}
        title="当前没有达标龙头候选"
        reason={runId ? `运行 ${runId} 未产出候选` : "今日可能尚未运行推荐"}
        action={
          <button className="btn btn--primary" onClick={() => navigate("/run")}>
            去运行推荐
          </button>
        }
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      <FilterBar
        total={items.length}
        filtered={filtered.length}
        filter={filter}
        onChange={setFilter}
      />

      <SectorDiversityBar items={filtered} />

      {expanded.size > 3 && (
        <div className="tag tag--warning" style={{ alignSelf: "flex-start" }}>
          已展开 {expanded.size} 行，可能超出可视范围
        </div>
      )}

      {filtered.map((c, i) => {
        const isOpen = expanded.has(c.recommendation_id);
        return (
          <div
            key={c.recommendation_id}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--line)",
              borderRadius: "var(--radius-lg)",
              overflow: "hidden",
            }}
          >
            <button
              onClick={() => toggle(c.recommendation_id)}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: "var(--space-3)",
                flexWrap: "wrap",
                padding: "var(--space-3) var(--space-4)",
                background: "transparent",
                border: "none",
                textAlign: "left",
                cursor: "pointer",
              }}
            >
              {/* 身份区：排名 + 代码徽标 + 名称 —— 始终在左、永不裁切 */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-2)",
                  minWidth: 0,
                  flex: "1 1 220px",
                }}
              >
                <span className="text-mono text-muted" style={{ fontSize: "var(--text-sm)", flexShrink: 0 }}>
                  #{i + 1}
                </span>
                <StockTicker code={c.code} />
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontWeight: 600,
                      color: "var(--ink)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {c.name}
                  </div>
                  {c.entry_rule && (
                    <div
                      style={{
                        fontSize: "var(--text-xs)",
                        color: "var(--text-muted)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        marginTop: 2,
                      }}
                    >
                      入场 · {c.entry_rule}
                    </div>
                  )}
                </div>
              </div>

              {/* 次级信息组：交易参数 + 评分 + 风险 + 展开箭头 —— 窄屏自动换行到第二行 */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-3)",
                  flexWrap: "wrap",
                  marginLeft: "auto",
                }}
              >
                <TradeParamsInline item={c} />
                <span className="text-mono" style={{ fontWeight: 700, color: "var(--accent-strong)", fontSize: "var(--text-lg)" }}>
                  {c.final_score.toFixed(0)}
                </span>
                <LlmStanceBadge stance={c.llm_stance} delta={c.llm_delta} />
                <RiskTag level={c.risk_level} setup={c.setup_type} />
                {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </div>
            </button>

            {isOpen && (
              <PoolExpandedRow item={c} onReplay={() => navigate("/history", { params: { recommendation_id: c.recommendation_id } })} onViewHistory={() => navigateSection("history")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 板块分散度指示器 —— 显示行业分布，过度集中时警告 */
function SectorDiversityBar({ items }: { items: CockpitRecommendation[] }) {
  if (items.length === 0) return null;

  const industryCount: Record<string, number> = {};
  for (const c of items) {
    const ind = c.industry || "未知";
    industryCount[ind] = (industryCount[ind] || 0) + 1;
  }

  const industries = Object.entries(industryCount).sort((a, b) => b[1] - a[1]);
  const maxCount = industries[0]?.[1] || 0;
  const maxRatio = maxCount / items.length;
  const isConcentrated = maxRatio > 0.5 && items.length > 2;

  return (
    <div
      className="panel"
      style={{
        padding: "var(--space-2) var(--space-4)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        flexWrap: "wrap",
        fontSize: "var(--text-sm)",
      }}
    >
      <span className="text-muted">板块分散度</span>
      <span className="text-mono" style={{ fontWeight: 600 }}>
        {items.length} 只 / {industries.length} 行业
      </span>
      {isConcentrated && (
        <span
          className="tag tag--warning"
          style={{ fontSize: "var(--text-xs)" }}
          title={`${industries[0][0]} 占比 ${(maxRatio * 100).toFixed(0)}%，过度集中`}
        >
          ⚠ {industries[0][0]} {maxCount}/{items.length} ({(maxRatio * 100).toFixed(0)}%)
        </span>
      )}
      <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-xs)" }}>
        {industries.slice(0, 4).map(([ind, cnt]) => `${ind} ${cnt}`).join(" · ")}
      </span>
    </div>
  );
}

function FilterBar({
  total,
  filtered,
  filter,
  onChange,
}: {
  total: number;
  filtered: number;
  filter: { risk?: RiskLevel; q: string };
  onChange: (f: { risk?: RiskLevel; q: string }) => void;
}) {
  const riskOptions: Array<{ label: string; value?: RiskLevel }> = [
    { label: "全部" },
    { label: "低风险", value: "low" },
    { label: "中风险", value: "medium" },
    { label: "高风险", value: "high" },
  ];
  return (
    <div
      className="panel"
      style={{
        padding: "var(--space-3) var(--space-4)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-3)",
        flexWrap: "wrap",
        position: "sticky",
        top: "calc(var(--topbar-height) + var(--space-3))",
        zIndex: "var(--z-sticky)",
      }}
    >
      <Search size={16} className="text-muted" />
      <input
        placeholder="搜索代码或名称…"
        value={filter.q}
        onChange={(e) => onChange({ ...filter, q: e.target.value })}
        style={{
          background: "var(--surface-soft)",
          border: "1px solid var(--line-strong)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-2) var(--space-3)",
          color: "var(--ink)",
          fontSize: "var(--text-sm)",
          minWidth: 200,
          fontFamily: "inherit",
        }}
      />
      <div style={{ display: "flex", gap: "var(--space-1)" }}>
        {riskOptions.map((r) => (
          <button
            key={r.label}
            className={`btn ${filter.risk === r.value ? "btn--primary" : "btn--ghost"}`}
            onClick={() => onChange({ ...filter, risk: r.value })}
            style={{ padding: "var(--space-1) var(--space-3)", fontSize: "var(--text-sm)" }}
          >
            {r.label}
          </button>
        ))}
      </div>
      <span className="text-muted" style={{ marginLeft: "auto", fontSize: "var(--text-sm)" }}>
        {filtered} / {total}
      </span>
    </div>
  );
}

/** LLM 态度徽章 —— 候选池卡片不展开即可见 LLM 审查的 stance + delta。
 * 支持/强支持绿色 +delta，反对/否决红色 -delta，中性灰色。无 LLM 审查时不显示。 */
function LlmStanceBadge({ stance, delta }: { stance?: string | null; delta?: number | null }) {
  if (!stance) return null;
  const isSupport = stance === "strong_support" || stance === "support";
  const isOppose = stance === "oppose" || stance === "veto";
  const color = isSupport ? "var(--market-up)" : isOppose ? "var(--market-down)" : "var(--text-muted)";
  const label =
    stance === "strong_support" ? "强支持"
    : stance === "support" ? "支持"
    : stance === "neutral_support" ? "中性偏多"
    : stance === "neutral" ? "中性"
    : stance === "oppose" ? "反对"
    : stance === "veto" ? "否决"
    : stance;
  return (
    <span
      className="text-mono"
      style={{
        fontSize: "var(--text-xs)",
        fontWeight: 600,
        color,
        padding: "2px 6px",
        borderRadius: "var(--radius-sm, 4px)",
        border: `1px solid ${color}40`,
        whiteSpace: "nowrap",
      }}
      title={`LLM 审查: ${label}${delta != null ? ` (${delta >= 0 ? "+" : ""}${delta})` : ""}`}
    >
      {label}{delta != null ? ` ${delta >= 0 ? "+" : ""}${delta}` : ""}
    </span>
  );
}

function RiskTag({ level, setup }: { level: RiskLevel; setup?: string | null }) {
  const toneClass =
    level === "high" ? "tag--negative" : level === "medium" ? "tag--warning" : "tag--positive";
  const color =
    level === "high" ? "var(--market-up)" : level === "medium" ? "var(--warning)" : "var(--market-down)";
  return (
    <div style={{ display: "flex", gap: "var(--space-1)" }}>
      {setup && <span className="tag">{setup}</span>}
      <span className={`tag ${toneClass}`} style={{ color }}>{level}</span>
    </div>
  );
}

/**
 * 行内交易参数 —— 推荐价/止损/止盈一屏可见，等宽数字右对齐。
 * A 股涨红跌绿：止盈(上行目标)=红、止损(下行保护)=绿，低饱和。
 * 让用户在展开前即可看到风控报价，无需逐行点开。
 */
function TradeParamsInline({ item }: { item: CockpitRecommendation }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "var(--space-3)" }}>
      <TradeParam label="推荐" value={item.recommend_price} />
      <TradeParam label="止损" value={item.stop_loss} tone="down" />
      <TradeParam label="止盈" value={item.take_profit} tone="up" />
    </div>
  );
}

function TradeParam({
  label,
  value,
  tone,
}: {
  label: string;
  value?: number | null;
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
    <div style={{ display: "inline-flex", alignItems: "baseline", gap: 4 }}>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono, monospace)", fontSize: "var(--text-sm)", fontWeight: 600, color }}>
        {value != null ? value.toFixed(2) : "—"}
      </span>
    </div>
  );
}
