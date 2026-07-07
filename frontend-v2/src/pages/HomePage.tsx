import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ChevronRight,
  PlayCircle,
  TrendingUp,
} from "lucide-react";
import { api } from "../api/client";
import type { CockpitRecommendation, DashboardOverview, HomeBootstrap } from "../types";
import { CardSkeleton, EmptyState, ErrorPage } from "../components/StateViews";
import {
  LatestRunCard,
  MarketBreadthPanel,
  QuickNavGrid,
  Sep,
  SkillStatusStrip,
} from "../components/HomePanels";
import { StockTicker } from "../components/StockTicker";
import { CollapsiblePanel } from "../components/CollapsiblePanel";
import { useUrlRoute } from "../app/useUrlRoute";

/**
 * 驾驶舱首页 —— 状态条 + 温度面板 + 运行状态/龙头候选 + 最近产出/工作区/策略开关
 */
export function HomePage() {
  const { navigate, navigateSection } = useUrlRoute();
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 合并自着陆页的独有数据：最近 run + 策略开关状态 + 市场宽度
  const [boot, setBoot] = useState<HomeBootstrap | null>(null);

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

  useEffect(() => {
    api
      .bootstrap()
      .then((d) => setBoot(d))
      .catch(() => {
        /* 着陆页数据非关键，失败则静默降级，不阻塞驾驶舱 */
      });
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

      {/* 温度 —— 原市场宽度，提升为驾驶舱首屏核心指标 */}
      {boot && (
        <CollapsiblePanel kicker="温度" title="今日涨跌家数">
          <MarketBreadthPanel snapshot={boot.market_snapshot} />
        </CollapsiblePanel>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) minmax(0, 3fr)",
          gap: "var(--space-4)",
          alignItems: "start",
        }}
      >
        {/* 左列：运行状态（已移除 MarketTempPanel） */}
        <RunStatusPanel run={current_run} hasRun={hasRun} canRun={canRun} onRun={() => navigate("/run")} onViewHistory={() => navigateSection("history")} />

        {/* 右列：龙头候选（视觉重心） */}
        <CandidatePanel items={recommendations} onViewAll={() => navigateSection("pool")} />
      </div>

      {/* 合并自着陆页的独有内容：最近产出 / 工作区 / 策略开关 */}
      {boot && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
              gap: "var(--space-4)",
              alignItems: "start",
            }}
          >
            <CollapsiblePanel kicker="最近产出" title="最新推荐 run">
              <LatestRunCard
                run={boot.recent_runs.formal ?? boot.recent_runs.experiment}
                onOpen={() => navigateSection("dashboard")}
              />
            </CollapsiblePanel>

            <CollapsiblePanel kicker="工作区" title="快速进入">
              <QuickNavGrid onNavigate={navigateSection} />
            </CollapsiblePanel>
          </div>

          <CollapsiblePanel kicker="系统" title="策略开关状态">
            <SkillStatusStrip summary={boot.skill_summary} />
          </CollapsiblePanel>
        </>
      )}
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
    <CollapsiblePanel
      kicker="运行状态"
      title={
        <>
          <Activity size={16} />
          {hasRun ? "今日已运行" : run.status === "running" ? "运行中" : "今日未运行"}
        </>
      }
      collapsedKey="home-status"
    >
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
    </CollapsiblePanel>
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
    <CollapsiblePanel
      kicker="龙头候选 · 今日 top 5"
      title={
        <>
          <TrendingUp size={16} />
          候选 {items.length} 只
        </>
      }
      collapsedKey="home-candidates"
      className="panel--min360"
      actions={
        <button className="btn btn--ghost" onClick={onViewAll}>
          查看全部
          <ChevronRight size={14} />
        </button>
      }
    >
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
    </CollapsiblePanel>
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
            <StockTicker code={item.code} />
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

      {item.recommend_price != null || item.stop_loss != null || item.take_profit != null ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: "var(--space-1)",
            paddingTop: "var(--space-2)",
            borderTop: "1px solid var(--line)",
          }}
        >
          <MiniMetric label="推荐" value={item.recommend_price} />
          <MiniMetric label="止损" value={item.stop_loss} tone="down" />
          <MiniMetric label="止盈" value={item.take_profit} tone="up" />
        </div>
      ) : null}

      {item.entry_rule ? (
        <div
          title={item.entry_rule}
          style={{
            fontSize: 10,
            lineHeight: 1.3,
            color: "var(--text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          入场 · {item.entry_rule}
        </div>
      ) : null}
    </div>
  );
}

/** 候选卡内联交易参数 —— 等宽数字 + 涨红跌绿（A 股语义），一眼可见决策价 */
function MiniMetric({
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
    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{label}</span>
      <span className="text-mono" style={{ fontSize: 13, fontWeight: 600, color, lineHeight: 1 }}>
        {value != null ? value.toFixed(2) : "—"}
      </span>
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
      {/* 温度面板 */}
      <CardSkeleton count={1} />
      {/* 运行状态 + 龙头候选 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 3fr", gap: "var(--space-4)" }}>
        <CardSkeleton count={1} />
        <CardSkeleton count={2} />
      </div>
    </div>
  );
}
