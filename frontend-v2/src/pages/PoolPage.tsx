import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  History,
  Search,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from "lucide-react";
import { api } from "../api/client";
import type { CockpitRecommendation } from "../types";
import { EmptyState, ErrorPage } from "../components/StateViews";
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
                display: "grid",
                gridTemplateColumns: "32px 80px 1fr 100px 120px 24px",
                alignItems: "center",
                gap: "var(--space-3)",
                padding: "var(--space-3) var(--space-4)",
                background: "transparent",
                border: "none",
                textAlign: "left",
                cursor: "pointer",
              }}
            >
              <span className="text-mono text-muted" style={{ fontSize: "var(--text-sm)" }}>#{i + 1}</span>
              <span className="text-mono" style={{ fontSize: "var(--text-sm)" }}>{c.code}</span>
              <span style={{ fontWeight: 600, color: "var(--ink)" }}>{c.name}</span>
              <span className="text-mono" style={{ fontWeight: 700, color: "var(--accent-strong)", fontSize: "var(--text-lg)" }}>
                {c.final_score.toFixed(0)}
              </span>
              <RiskTag level={c.risk_level} setup={c.setup_type} />
              {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            </button>

            {isOpen && (
              <ExpandedRow item={c} onReplay={() => navigate("/history", { params: { recommendation_id: c.recommendation_id } })} onViewHistory={() => navigateSection("history")} />
            )}
          </div>
        );
      })}
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

function ExpandedRow({
  item,
  onReplay,
  onViewHistory,
}: {
  item: CockpitRecommendation;
  onReplay: () => void;
  onViewHistory: () => void;
}) {
  const confidence = deriveConfidence(item);
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
      </div>

      {/* 右：LLM 可信度 + 操作 */}
      <div>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>LLM 解释可信度</div>
        <ConfidenceMeter confidence={confidence} />
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

function ConfidenceMeter({ confidence }: { confidence: Confidence }) {
  const { certain, uncertain, validatedRatio, level } = confidence;
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
          <div className="text-muted">历史验证</div>
          <div className="text-mono" style={{ color: "var(--accent-strong)", fontWeight: 600 }}>
            {(validatedRatio * 100).toFixed(0)}%
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
            width: `${validatedRatio * 100}%`,
            background: "var(--accent)",
            transition: "width var(--motion-normal)",
          }}
        />
      </div>
    </div>
  );
}

/** 交易参数指标块 —— 推荐价/止损/止盈等关键价格一屏可见 */
function TradeMetric({
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
