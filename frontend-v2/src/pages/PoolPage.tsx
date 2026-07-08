import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock,
  Search,
} from "lucide-react";
import { api } from "../api/client";
import type { CockpitRecommendation, OutcomeSummary } from "../types";
import { EmptyState, ErrorPage } from "../components/StateViews";
import { PoolExpandedRow } from "../components/PoolExpandedRow";
import { StockTicker } from "../components/StockTicker";
import { LlmStanceBadge } from "../components/LlmStanceBadge";
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

  // 每只股票已结算推荐的真实历史表现（胜率/均收益），用于展开行「历史验证」替换启发式
  const codes = useMemo(
    () => (items ? Array.from(new Set(items.map((c) => c.code))) : []),
    [items],
  );
  const codesKey = codes.join(",");
  const { data: outcomeSummary } = useApi<Record<string, OutcomeSummary>>(
    () => api.recommendationOutcomeSummary(codes),
    [codesKey],
  );

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

  // 从候选列表中提取本批共同的推荐时间信息（同一 run 共享 trade_date / recommended_at）
  // deriveBatchMeta 是纯函数且只需取首元素，无需 useMemo（避免在条件 return 后违反 Hooks 规则）
  const batchMeta = deriveBatchMeta(items);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      <RecommendationTimeBar meta={batchMeta} runId={runId} total={items.length} />
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
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-2)",
                    }}
                  >
                    {c.name}
                    {c.industry && (
                      <span className="tag" style={{ fontSize: "var(--text-xs)", flexShrink: 0 }}>
                        {c.industry}
                      </span>
                    )}
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
              <PoolExpandedRow item={c} outcomeSummary={outcomeSummary ?? {}} onReplay={() => navigate("/history", { params: { recommendation_id: c.recommendation_id } })} onViewHistory={() => navigateSection("history")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 本批候选的推荐时间信息（同一 run 共享） */
type BatchMeta = {
  trade_date: string;
  recommended_at?: string | null;
  run_name?: string | null;
  preset_name?: string | null;
  run_id?: string | null;
};

function deriveBatchMeta(items: CockpitRecommendation[] | null | undefined): BatchMeta | null {
  if (!items || items.length === 0) return null;
  const first = items[0];
  return {
    trade_date: first.trade_date,
    recommended_at: first.recommended_at ?? null,
    run_name: first.run_name ?? null,
    preset_name: first.preset_name ?? null,
    run_id: first.run_id ?? null,
  };
}

/** 推荐时间标注条 —— 明确标注这一批候选的推荐日期/时间、来源 run 与预设。 */
function RecommendationTimeBar({
  meta,
  runId,
  total,
}: {
  meta: BatchMeta | null;
  runId: string | null;
  total: number;
}) {
  if (!meta) return null;
  const parts: string[] = [];
  // 推荐日期（trade_date）
  if (meta.trade_date) parts.push(`推荐日期 ${meta.trade_date}`);
  // 推荐时刻（recommended_at 带时分，截取到分钟）
  if (meta.recommended_at) {
    const timeLabel = formatRecommendedAt(meta.recommended_at);
    if (timeLabel) parts.push(timeLabel);
  }
  // 预设 / 运行名称
  if (meta.preset_name) parts.push(`预设 ${meta.preset_name}`);
  if (meta.run_name && meta.run_name !== meta.preset_name) parts.push(meta.run_name);
  // run_id（URL 已带 runId 时不重复显示）
  const effectiveRunId = runId || meta.run_id;
  if (effectiveRunId) parts.push(`Run ${effectiveRunId.slice(-8)}`);

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
      <Clock size={14} className="text-muted" />
      <span className="text-muted">本批推荐</span>
      <span className="text-mono" style={{ fontWeight: 600, color: "var(--ink)" }}>
        {parts.length > 0 ? parts.join(" · ") : "—"}
      </span>
      <span className="tag" style={{ marginLeft: "auto", fontSize: "var(--text-xs)" }}>
        {total} 只候选
      </span>
    </div>
  );
}

/** 将 recommended_at ISO 字符串格式化为「推荐时刻 MM-DD HH:MM」。 */
function formatRecommendedAt(iso: string): string | null {
  // 期望形如 2026-07-08T14:45:00 或 2026-07-08T14:45:00.123456
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!match) return null;
  const [, , mm, dd, hh, min] = match;
  return `推荐时刻 ${mm}-${dd} ${hh}:${min}`;
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
