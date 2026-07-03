import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Flame,
  PlayCircle,
  TrendingUp,
} from "lucide-react";
import { api } from "../api/client";
import type { CockpitRecommendation, DashboardOverview } from "../types";
import { CardSkeleton, EmptyState, ErrorPage } from "../components/StateViews";
import { useUrlRoute } from "../app/useUrlRoute";

/**
 * 驾驶舱首页 —— 精简后的 2 列 3 面板 + 1 状态条
 * 见 docs/UX-architecture-full-report.md §6.2
 *
 * 只回答 30 秒内的三个问题：
 * 1. 今天能不能做？ → 市场温度（含风险信号）
 * 2. 跑出结果了吗？ → 运行状态 + 状态条
 * 3. 有哪些候选？ → 龙头候选卡
 */
export function HomePage() {
  const { navigate, navigateSection } = useUrlRoute();
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.dashboard
      .overview({ signal: controller.signal })
      .then((d) => setData(d))
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message || "加载失败");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  if (loading) return <HomePageSkeleton />;
  if (error) return <ErrorPage title="驾驶舱加载失败" message={error} onRetry={() => window.location.reload()} />;
  if (!data) return <EmptyState title="暂无驾驶舱数据" reason="后端可能尚未完成今日数据准备" />;

  const { market, risk_radar, current_run, data_health, recommendations } = data;
  const canRun = data_health.can_run_local_recommendation;
  const hasRun = !!current_run.run_id && current_run.status === "completed";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <StatusBar
        canRun={canRun}
        healthStatus={data_health.status}
        run={current_run}
        candidateCount={recommendations.length}
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)",
          gap: "var(--space-4)",
          alignItems: "start",
        }}
      >
        {/* 左列：市场温度 + 运行状态 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <MarketTempPanel market={market} radar={risk_radar} />
          <RunStatusPanel run={current_run} hasRun={hasRun} canRun={canRun} onRun={() => navigate("/run")} onViewHistory={() => navigateSection("history")} />
        </div>

        {/* 右列：龙头候选（视觉重心） */}
        <CandidatePanel items={recommendations} onViewAll={() => navigateSection("pool")} />
      </div>
    </div>
  );
}

// ─── 顶部状态条 ───────────────────────────────────────────────
function StatusBar({
  canRun,
  healthStatus,
  run,
  candidateCount,
}: {
  canRun: boolean;
  healthStatus: string;
  run: DashboardOverview["current_run"];
  candidateCount: number;
}) {
  const dot = canRun ? "🟢" : healthStatus === "blocked" ? "🔴" : "🟡";
  const readinessLabel = canRun ? "数据就绪" : healthStatus === "blocked" ? "数据不足" : "数据降级";
  const runLabel = run.run_id
    ? run.status === "completed"
      ? `今日已运行`
      : run.status === "running"
      ? "运行中"
      : run.status
    : "今日未运行";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-4)",
        padding: "var(--space-2) var(--space-4)",
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-lg)",
        position: "sticky",
        top: "calc(var(--topbar-height) + var(--space-3))",
        zIndex: "var(--z-sticky)",
        fontSize: "var(--text-sm)",
      }}
    >
      <span>{dot}</span>
      <strong style={{ color: "var(--ink)" }}>{readinessLabel}</strong>
      <Sep />
      <span className="text-muted">运行：</span>
      <strong style={{ color: "var(--ink)" }}>{runLabel}</strong>
      <Sep />
      <span className="text-muted">候选：</span>
      <strong className="text-mono" style={{ color: "var(--ink)" }}>{candidateCount} 只</strong>
      <Sep />
      <span className="text-muted">近 5 日胜率：</span>
      <strong className="text-mono" style={{ color: "var(--accent-strong)" }}>—</strong>
    </div>
  );
}

function Sep() {
  return <span style={{ color: "var(--line-strong)" }}>·</span>;
}

// ─── 市场温度面板（合并风险雷达） ─────────────────────────────
function MarketTempPanel({
  market,
  radar,
}: {
  market: DashboardOverview["market"];
  radar: DashboardOverview["risk_radar"];
}) {
  const heat = market.heat_score ?? 0;
  const regimeLabel = regimeToLabel(market.regime);
  const upRatio = (market.up_ratio ?? 0) * 100;
  const isHot = heat >= 60;
  const isCold = heat < 30;

  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">市场温度</div>
          <h3 className="panel__title">
            <Flame size={16} style={{ color: isHot ? "var(--warning)" : "var(--muted)" }} />
            {regimeLabel}
          </h3>
        </div>
        <div className="text-mono text-muted" style={{ fontSize: "var(--text-xs)" }}>
          {market.trade_date}
        </div>
      </div>

      {/* 温度计 */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "var(--space-2)",
            marginBottom: "var(--space-2)",
          }}
        >
          <span className="text-mono" style={{ fontSize: "var(--text-3xl)", fontWeight: 700, color: "var(--ink)" }}>
            {heat.toFixed(0)}
          </span>
          <span className="text-muted" style={{ fontSize: "var(--text-sm)" }}>/ 100</span>
        </div>
        <div
          style={{
            height: 6,
            background: "var(--surface-soft)",
            borderRadius: "var(--radius-full)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${heat}%`,
              background: isCold
                ? "var(--market-down)"
                : isHot
                ? "var(--market-up)"
                : "var(--accent)",
              transition: "width var(--motion-normal)",
            }}
          />
        </div>
      </div>

      {/* 信号指标行（原风险雷达的能力合并到这里） */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-2)" }}>
        <Metric label="涨跌比" value={`${upRatio.toFixed(0)}%`} tone={upRatio >= 50 ? "up" : "down"} />
        <Metric label="涨停" value={market.limit_up_count} tone="up" />
        <Metric label="跌停" value={market.limit_down_count} tone="down" />
        <Metric label="风险信号" value={radar.high + radar.medium} tone={radar.high > 0 ? "warn" : "neutral"} />
      </div>

      {market.main_industries.length > 0 && (
        <div style={{ marginTop: "var(--space-4)" }}>
          <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>主线</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
            {market.main_industries.slice(0, 5).map((ind) => (
              <span key={ind} className="tag">
                {ind}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "up" | "down" | "warn" | "neutral";
}) {
  const color =
    tone === "up"
      ? "var(--market-down)"
      : tone === "down"
      ? "var(--market-up)"
      : tone === "warn"
      ? "var(--warning)"
      : "var(--ink)";
  return (
    <div
      style={{
        padding: "var(--space-2) var(--space-3)",
        background: "var(--surface-soft)",
        borderRadius: "var(--radius-md)",
      }}
    >
      <div className="text-muted" style={{ fontSize: "var(--text-xs)", marginBottom: 2 }}>
        {label}
      </div>
      <div className="text-mono" style={{ fontWeight: 600, color }}>
        {value}
      </div>
    </div>
  );
}

function regimeToLabel(regime: string): string {
  const map: Record<string, string> = {
    strong_bull: "强势·牛市",
    bull: "牛市",
    neutral: "震荡",
    bear: "熊市",
    strong_bear: "强势·熊市",
    climax: "高潮",
  };
  return map[regime] ?? regime ?? "—";
}

// ─── 运行状态面板 ─────────────────────────────────────────────
function RunStatusPanel({
  run,
  hasRun,
  canRun,
  onRun,
  onViewHistory,
}: {
  run: DashboardOverview["current_run"];
  hasRun: boolean;
  canRun: boolean;
  onRun: () => void;
  onViewHistory: () => void;
}) {
  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">运行状态</div>
          <h3 className="panel__title">
            <Activity size={16} />
            {hasRun ? "今日已运行" : run.status === "running" ? "运行中" : "今日未运行"}
          </h3>
        </div>
      </div>

      <div style={{ fontSize: "var(--text-sm)", color: "var(--ink-secondary)", marginBottom: "var(--space-4)" }}>
        {run.message || "暂无运行消息"}
      </div>

      {!hasRun && (
        <button
          className="btn btn--primary"
          disabled={!canRun}
          onClick={onRun}
          style={{ width: "100%", justifyContent: "center", padding: "var(--space-3)" }}
        >
          <PlayCircle size={16} />
          {canRun ? "运行今日推荐" : "数据不足，无法运行"}
        </button>
      )}

      {hasRun && (
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button className="btn" onClick={onRun} style={{ flex: 1, justifyContent: "center" }}>
            <PlayCircle size={14} />
            重新运行
          </button>
          <button className="btn btn--ghost" onClick={onViewHistory}>
            查看历史
          </button>
        </div>
      )}
    </section>
  );
}

// ─── 龙头候选面板（视觉重心） ─────────────────────────────────
function CandidatePanel({
  items,
  onViewAll,
}: {
  items: CockpitRecommendation[];
  onViewAll: () => void;
}) {
  const top = items.slice(0, 5);

  return (
    <section className="panel" style={{ minHeight: 360 }}>
      <div className="panel__header">
        <div>
          <div className="panel__kicker">龙头候选 · 今日 top 5</div>
          <h3 className="panel__title">
            <TrendingUp size={16} />
            候选 {items.length} 只
          </h3>
        </div>
        <button className="btn btn--ghost" onClick={onViewAll}>
          查看全部
          <ChevronRight size={14} />
        </button>
      </div>

      {top.length === 0 ? (
        <EmptyState
          icon={<AlertTriangle size={28} />}
          title="当前没有达标龙头候选"
          reason="可能因为市场温度过低、过滤条件严格或今日尚未运行推荐"
          action={
            <button className="btn btn--primary" onClick={onViewAll}>
              去候选池
            </button>
          }
        />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "var(--space-3)" }}>
          {top.map((c, i) => (
            <CandidateCard key={c.recommendation_id} item={c} rank={i + 1} />
          ))}
        </div>
      )}

      {top.length > 0 && (
        <div
          style={{
            marginTop: "var(--space-4)",
            padding: "var(--space-2) var(--space-3)",
            background: "var(--surface-soft)",
            borderRadius: "var(--radius-md)",
            display: "flex",
            justifyContent: "space-between",
            fontSize: "var(--text-xs)",
            color: "var(--muted)",
          }}
        >
          <span>KPI · 候选 {items.length} 只</span>
          <span>均分 {avgScore(items).toFixed(0)}</span>
          <span>通过率 {passRate(items).toFixed(0)}%</span>
        </div>
      )}
    </section>
  );
}

function CandidateCard({ item, rank }: { item: CockpitRecommendation; rank: number }) {
  const riskTone =
    item.risk_level === "high"
      ? "var(--market-up)"
      : item.risk_level === "medium"
      ? "var(--warning)"
      : "var(--market-down)";
  const toneClass =
    item.risk_level === "high" ? "tag--negative" : item.risk_level === "medium" ? "tag--warning" : "tag--positive";

  return (
    <div
      style={{
        padding: "var(--space-3)",
        background: "var(--surface-soft)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div className="text-muted text-mono" style={{ fontSize: "var(--text-xs)" }}>
            #{rank} {item.code}
          </div>
          <div style={{ fontWeight: 600, color: "var(--ink)" }}>{item.name}</div>
        </div>
        <div className="text-mono" style={{ fontSize: "var(--text-lg)", fontWeight: 700, color: "var(--accent-strong)" }}>
          {item.final_score.toFixed(0)}
        </div>
      </div>
      <div style={{ display: "flex", gap: "var(--space-1)", flexWrap: "wrap" }}>
        {item.setup_type && <span className="tag">{item.setup_type}</span>}
        <span className={`tag ${toneClass}`} style={{ color: riskTone }}>
          {item.risk_level}
        </span>
      </div>
    </div>
  );
}

// ─── 工具 ─────────────────────────────────────────────────────
function avgScore(items: CockpitRecommendation[]): number {
  if (!items.length) return 0;
  return items.reduce((s, c) => s + c.final_score, 0) / items.length;
}

function passRate(items: CockpitRecommendation[]): number {
  if (!items.length) return 0;
  const passed = items.filter((c) => c.status !== "rejected").length;
  return (passed / items.length) * 100;
}

function HomePageSkeleton() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <div className="skeleton" style={{ height: 40, borderRadius: "var(--radius-lg)" }} />
      <div style={{ display: "grid", gridTemplateColumns: "2fr 3fr", gap: "var(--space-4)" }}>
        <CardSkeleton count={2} />
        <CardSkeleton count={3} />
      </div>
    </div>
  );
}
