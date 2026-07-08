import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ChevronRight,
  Database,
  PlayCircle,
  TrendingUp,
} from "lucide-react";
import { api } from "../api/client";
import type {
  CockpitRecommendation,
  DashboardOverview,
  DataHealthDiagnostic,
  HomeBootstrap,
} from "../types";
import { CardSkeleton, EmptyState, ErrorPage } from "../components/StateViews";
import {
  LatestRunCard,
  MarketBreadthPanel,
  Sep,
} from "../components/HomePanels";
import { StockTicker } from "../components/StockTicker";
import { CollapsiblePanel } from "../components/CollapsiblePanel";
import { LlmStanceBadge } from "../components/LlmStanceBadge";
import { useUrlRoute } from "../app/useUrlRoute";

/**
 * 驾驶舱首页 —— 状态条 + 温度面板 + [运行状态/最近产出 | 龙头候选]
 */
export function HomePage() {
  const { navigate, navigateSection } = useUrlRoute();
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 合并自着陆页的独有数据：最近 run + 策略开关状态 + 市场宽度
  const [boot, setBoot] = useState<HomeBootstrap | null>(null);
  // 数据健康度诊断（独立于 overview，调用 /api/admin/data-health）
  const [healthDiag, setHealthDiag] = useState<DataHealthDiagnostic | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);

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

  useEffect(() => {
    const controller = new AbortController();
    setHealthLoading(true);
    api
      .dataHealth({ signal: controller.signal })
      .then((d) => setHealthDiag(d))
      .catch(() => {
        /* 数据健康度诊断非阻塞，失败则静默降级 */
      })
      .finally(() => setHealthLoading(false));
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
        healthDiag={healthDiag}
        healthLoading={healthLoading}
        onViewHealthDetail={() => navigate("/data-health")}
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
        {/* 左列：运行状态 + 数据健康度 + 最近产出 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <RunStatusPanel run={current_run} hasRun={hasRun} canRun={canRun} onRun={() => navigate("/run")} onViewHistory={() => navigateSection("history")} />

          {boot && (
            <CollapsiblePanel kicker="最近产出" title="最新推荐 run">
              <LatestRunCard
                run={boot.recent_runs.latest}
                onOpen={() => navigateSection("dashboard")}
              />
            </CollapsiblePanel>
          )}
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
  healthDiag,
  healthLoading,
  onViewHealthDetail,
}: {
  canRun: boolean;
  healthStatus: string;
  run: DashboardOverview["current_run"];
  candidateCount: number;
  healthDiag: DataHealthDiagnostic | null;
  healthLoading: boolean;
  onViewHealthDetail: () => void;
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

  // 健康度指示器数据
  const score = healthDiag?.score ?? 0;
  const scoreColor =
    score >= 90 ? "var(--market-down)" : score >= 70 ? "var(--warning)" : "var(--market-up)";
  const scoreLabel = score >= 90 ? "健康" : score >= 70 ? "需关注" : "异常";
  const issues = healthDiag?.issues ?? [];
  const highIssues = issues.filter((i) => i.severity === "high");

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
      {/* 左侧：核心状态信息 */}
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

      {/* 右侧：健康度指示器（弹性推到最右） */}
      <div style={{ marginLeft: "auto", flexShrink: 0 }}>
        <button
          onClick={onViewHealthDetail}
          className="btn"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-2)",
            padding: "3px 12px",
            borderRadius: "var(--radius-full)",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
            background:
              score >= 90
                ? "color-mix(in srgb, var(--market-down) 10%, transparent)"
                : score >= 70
                  ? "color-mix(in srgb, var(--warning) 10%, transparent)"
                  : "color-mix(in srgb, var(--market-up) 10%, transparent)",
            border: "1px solid " + (score >= 90 ? "color-mix(in srgb, var(--market-down) 25%, transparent)" : score >= 70 ? "color-mix(in srgb, var(--warning) 25%, transparent)" : "color-mix(in srgb, var(--market-up) 25%, transparent)"),
            color: scoreColor,
            cursor: "pointer",
            transition: "all 150ms ease",
          }}
          title={issues.length > 0 ? `${issues.length} 个问题 (${highIssues.length} 高危)` : "数据健康度"}
        >
          {/* 脉冲动画：有异常时 */}
          {highIssues.length > 0 && (
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--market-up)",
                display: "inline-block",
                animation: "health-pulse 1.5s ease-in-out infinite",
              }}
            />
          )}
          {healthLoading ? (
            <>诊断中…</>
          ) : (
            <>
              <Database size={13} />
              <span className="text-mono" style={{ fontWeight: 700, letterSpacing: "-0.02em" }}>
                {score}
              </span>
              <span>{scoreLabel}</span>
              {issues.length > 0 && (
                <span
                  style={{
                    marginLeft: 2,
                    opacity: 0.7,
                    fontSize: 10,
                  }}
                >
                  · {issues.length} 问题
                </span>
              )}
              <ChevronRight size={12} style={{ marginLeft: -2 }} />
            </>
          )}
        </button>
      </div>

      {/* 注入脉冲关键帧（仅渲染一次） */}
      <style>{`
        @keyframes health-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.85); }
        }
      `}</style>
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

// ─── 数据健康度卡片 ─────────────────────────────────────────
function DataHealthCard({
  diag,
  loading,
  onViewDetail,
}: {
  diag: DataHealthDiagnostic | null;
  loading: boolean;
  onViewDetail: () => void;
}) {
  const score = diag?.score ?? 0;
  const scoreColor =
    score >= 90 ? "var(--market-up)" : score >= 70 ? "var(--warning)" : "var(--market-down)";
  const scoreLabel = score >= 90 ? "健康" : score >= 70 ? "需关注" : "异常";
  const refDate = diag?.reference_trade_date || "—";
  const refSource = diag?.reference_trade_date_source || "";
  const tables = diag?.tables || {};
  const lagDays = diag?.lag_days || {};
  const issues = diag?.issues || [];

  // 按关键表优先排序，取前 6 个
  const tableRows = Object.entries(tables)
    .map(([key, info]) => ({
      key,
      label: info?.label || key,
      maxDate: info?.max_date || "—",
      lag: lagDays[key] ?? null,
      critical: info?.critical ?? false,
      totalRows: info?.total_rows ?? 0,
      error: info?.error ?? null,
    }))
    .sort((a, b) => {
      if (a.critical !== b.critical) return a.critical ? -1 : 1;
      const aLag = a.lag ?? 999;
      const bLag = b.lag ?? 999;
      return bLag - aLag;
    })
    .slice(0, 6);

  const highIssues = issues.filter((i) => i.severity === "high");
  const mediumIssues = issues.filter((i) => i.severity === "medium");
  const topIssues = [...highIssues, ...mediumIssues].slice(0, 4);

  return (
    <CollapsiblePanel
      kicker="数据健康度"
      title={
        <>
          <Database size={16} />
          {loading ? "诊断中…" : `${score} 分 · ${scoreLabel}`}
        </>
      }
      collapsedKey="home-data-health"
      actions={
        <button className="btn btn--ghost" onClick={onViewDetail}>
          详情
          <ChevronRight size={14} />
        </button>
      }
    >
      {/* 评分 + 参考交易日 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-4)",
          marginBottom: "var(--space-3)",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", minWidth: 64 }}>
          <span className="text-mono" style={{ fontSize: "var(--text-2xl)", fontWeight: 700, color: scoreColor }}>
            {loading ? "—" : score}
          </span>
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>健康评分</span>
        </div>
        <div style={{ flex: 1, fontSize: "var(--text-sm)", color: "var(--ink-secondary)" }}>
          <div>
            参考交易日 <strong className="text-mono">{refDate}</strong>
            {refSource && <span className="text-muted"> ({refSource})</span>}
          </div>
          {!loading && issues.length > 0 && (
            <div className="text-muted" style={{ marginTop: 2 }}>
              {highIssues.length > 0 && (
                <span style={{ color: "var(--market-down)" }}>高 {highIssues.length} </span>
              )}
              {mediumIssues.length > 0 && (
                <span style={{ color: "var(--warning)" }}>中 {mediumIssues.length} </span>
              )}
              共 {issues.length} 个问题
            </div>
          )}
        </div>
      </div>

      {/* 各表覆盖情况 */}
      {tableRows.length > 0 && (
        <div
          style={{
            fontSize: "var(--text-xs)",
            borderTop: "1px solid var(--line)",
            paddingTop: "var(--space-2)",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) auto auto",
              gap: "0 var(--space-3)",
              color: "var(--text-muted)",
              marginBottom: "var(--space-1)",
            }}
          >
            <span>数据表</span>
            <span style={{ textAlign: "right" }}>最新日期</span>
            <span style={{ textAlign: "right", minWidth: 36 }}>滞后</span>
          </div>
          {tableRows.map((row) => {
            const lagColor =
              row.lag == null
                ? "var(--text-muted)"
                : row.lag <= 0
                  ? "var(--market-up)"
                  : row.lag <= 2
                    ? "var(--warning)"
                    : "var(--market-down)";
            return (
              <div
                key={row.key}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) auto auto",
                  gap: "0 var(--space-3)",
                  padding: "2px 0",
                  alignItems: "center",
                }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {row.critical && <span style={{ color: "var(--accent-strong)" }}>● </span>}
                  {row.label}
                </span>
                <span className="text-mono" style={{ textAlign: "right", color: "var(--ink-secondary)" }}>
                  {row.maxDate}
                </span>
                <span className="text-mono" style={{ textAlign: "right", color: lagColor, minWidth: 36 }}>
                  {row.lag == null ? "—" : `${row.lag}d`}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* 问题列表 */}
      {topIssues.length > 0 && (
        <div
          style={{
            marginTop: "var(--space-2)",
            paddingTop: "var(--space-2)",
            borderTop: "1px solid var(--line)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {topIssues.map((issue, idx) => (
            <div
              key={idx}
              style={{
                fontSize: 11,
                lineHeight: 1.4,
                color: "var(--ink-secondary)",
                display: "flex",
                gap: "var(--space-1)",
              }}
            >
              <span
                style={{
                  color: issue.severity === "high" ? "var(--market-down)" : "var(--warning)",
                  flexShrink: 0,
                }}
              >
                {issue.severity === "high" ? "🔴" : "🟡"}
              </span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                {issue.message}
              </span>
            </div>
          ))}
          {issues.length > topIssues.length && (
            <span className="text-muted" style={{ fontSize: 10 }}>
              还有 {issues.length - topIssues.length} 个问题，点击「详情」查看
            </span>
          )}
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
      <div style={{ display: "flex", gap: "var(--space-1)", flexWrap: "wrap", alignItems: "center" }}>
        {item.setup_type && <span className="tag">{item.setup_type}</span>}
        <span className={`tag ${toneClass}`} style={{ color: riskTone }}>
          {item.risk_level}
        </span>
        <LlmStanceBadge stance={item.llm_stance} delta={item.llm_delta} />
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
      {/* 运行状态 + 最近产出 | 龙头候选 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 3fr", gap: "var(--space-4)" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <CardSkeleton count={1} />
          <CardSkeleton count={1} />
        </div>
        <CardSkeleton count={2} />
      </div>
    </div>
  );
}
