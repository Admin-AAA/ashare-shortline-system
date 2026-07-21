import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Brain,
  ChevronRight,
  Database,
  PlayCircle,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { api } from "../api/client";
import type {
  CockpitRecommendation,
  DashboardOverview,
  DataHealthDiagnostic,
  HomeBootstrap,
  LlmReviewHealth,
  OutcomeSummary,
  RunProgress,
  WinRateSummary,
} from "../types";
import { CardSkeleton, EmptyState, ErrorPage } from "../components/StateViews";
import {
  LatestRunCard,
  MarketBreadthPanel,
  Sep,
} from "../components/HomePanels";
import { StockTicker } from "../components/StockTicker";
import { Panel } from "../components/Panel";
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

  // 近 5 交易日 formal 推荐聚合胜率（驾驶舱状态条「近 5 日胜率」真实数据源，替代原硬编码占位 —）
  const [winRate, setWinRate] = useState<WinRateSummary | null>(null);
  // LLM 审查层健康度（驾驶舱状态条「AI 审查」徽章 — 北极星 B 项可见性缺口修复）
  const [llmHealth, setLlmHealth] = useState<LlmReviewHealth | null>(null);
  // 候选股票历史已结算推荐胜率（北极星 B 项「历史同策略胜率」一屏可见）
  const [outcomeMap, setOutcomeMap] = useState<Record<string, OutcomeSummary>>({});

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

  useEffect(() => {
    const controller = new AbortController();
    api
      .recommendationWinRate(5)
      .then((d) => setWinRate(d))
      .catch(() => {
        /* 胜率非阻塞，失败则保持 null（状态条显示 —），不阻塞驾驶舱 */
      });
    return () => controller.abort();
  }, []);

  // LLM 审查层健康度（非阻塞，失败则保持 null，状态条不显示该徽章）
  useEffect(() => {
    const controller = new AbortController();
    api
      .llmReviewHealth()
      .then((d) => setLlmHealth(d))
      .catch(() => {
        /* 审查健康度非阻塞，失败则静默降级 */
      });
    return () => controller.abort();
  }, []);

  // 候选股票历史已结算推荐胜率：recommendations 变化时按 codes 批量拉取（B 项历史同策略胜率）
  useEffect(() => {
    const codes = (data?.recommendations ?? []).map((r) => r.code).filter(Boolean);
    if (codes.length === 0) {
      setOutcomeMap({});
      return;
    }
    let cancelled = false;
    api.recommendationOutcomeSummary(codes).then((m) => {
      if (!cancelled) setOutcomeMap(m);
    }).catch(() => {
      /* 历史胜率非阻塞，失败则候选卡不显示徽章 */
    });
    return () => {
      cancelled = true;
    };
  }, [data?.recommendations]);

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
        winRate={winRate}
        llmHealth={llmHealth}
        onViewHealthDetail={() => navigate("/data-health")}
      />

      {/* 温度 —— 原市场宽度，提升为驾驶舱首屏核心指标 */}
      {boot && (
        <Panel kicker="温度" title="今日涨跌家数">
          <MarketBreadthPanel snapshot={boot.market_snapshot} />
        </Panel>
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
            <Panel kicker="最近产出" title="最新推荐 run">
              <LatestRunCard
                run={boot.recent_runs.latest}
                onOpen={() => navigateSection("dashboard")}
              />
            </Panel>
          )}
        </div>

        {/* 右列：龙头候选（视觉重心） */}
        <CandidatePanel
          items={recommendations}
          outcomeMap={outcomeMap}
          currentRun={current_run}
          marketRegime={market.regime}
          onViewAll={() => navigateSection("pool")}
          onViewHistory={() => navigateSection("history")}
        />
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
  winRate,
  llmHealth,
  onViewHealthDetail,
}: {
  canRun: boolean;
  healthStatus: string;
  run: DashboardOverview["current_run"];
  candidateCount: number;
  healthDiag: DataHealthDiagnostic | null;
  healthLoading: boolean;
  winRate: WinRateSummary | null;
  llmHealth: LlmReviewHealth | null;
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
      {winRate && winRate.win_rate != null ? (
        <strong
          className="text-mono"
          title={
            winRate.sample_count > 0
              ? `近 ${winRate.window_trade_days} 个已完成 formal 交易日 · 已结算 ${winRate.sample_count} 笔\n均收益 ${winRate.avg_return?.toFixed(2)}% (净 ${(winRate.net_avg_return ?? 0).toFixed(2)}%) · 盈亏比 ${winRate.profit_loss_ratio ?? "—"} (净 ${winRate.net_profit_loss_ratio ?? "—"})\n净口径扣 0.07%/笔（佣金万1双边 + 印花税卖出）`
              : "暂无已结算样本"
          }
          style={{
            color:
              winRate.win_rate >= 58
                ? "var(--market-up)"
                : winRate.win_rate >= 50
                  ? "var(--warning)"
                  : "var(--market-down)",
          }}
        >
          {winRate.win_rate.toFixed(1)}%
        </strong>
      ) : (
        <strong className="text-mono" style={{ color: "var(--muted)" }}>—</strong>
      )}
      {winRate && winRate.net_profit_loss_ratio != null && (
        <>
          <Sep />
          <span className="text-muted">净盈亏比：</span>
          <strong
            className="text-mono"
            title={
              winRate.pollution_n && winRate.pollution_n > 0 && winRate.clean_net_profit_loss_ratio != null
                ? `扣 0.07%/笔后盈亏比（D 项目标 ≥ 2.0）\n` +
                  `去污染后 ${winRate.clean_net_profit_loss_ratio.toFixed(2)}` +
                  (winRate.pollution_pl_delta != null
                    ? `（Δ${winRate.pollution_pl_delta >= 0 ? "+" : ""}${winRate.pollution_pl_delta.toFixed(2)}，剔除 pure 低线/soft@震荡 ${winRate.pollution_n} 票）`
                    : "") +
                  `\n契约层+soft ban 已拦新污染；历史票不重标，勿把污染当策略退化`
                : "扣 0.07%/笔交易成本后的盈亏比（D 项目标：净盈亏比 ≥ 2.0）"
            }
            style={{
              color:
                winRate.net_profit_loss_ratio >= 2.0
                  ? "var(--market-up)"
                  : winRate.net_profit_loss_ratio >= 1.4
                    ? "var(--warning)"
                    : "var(--market-down)",
            }}
          >
            {winRate.net_profit_loss_ratio.toFixed(2)}
          </strong>
          {/* 去污染可见：净 PL 被历史污染拖累时一屏提示，不改主数字、不刷样本 */}
          {winRate.pollution_n != null &&
            winRate.pollution_n > 0 &&
            winRate.clean_net_profit_loss_ratio != null &&
            (winRate.pollution_pl_delta ?? 0) >= 0.1 && (
              <>
                <Sep />
                <span className="text-muted">去污后：</span>
                <strong
                  className="text-mono"
                  title={
                    `剔除 pure 低线(<72) + pure soft@震荡 共 ${winRate.pollution_n} 独立票后的净盈亏比\n` +
                    `主口径 ${winRate.net_profit_loss_ratio.toFixed(2)} → 去污 ${winRate.clean_net_profit_loss_ratio.toFixed(2)}` +
                    (winRate.pollution_pl_delta != null
                      ? `（Δ+${winRate.pollution_pl_delta.toFixed(2)}）`
                      : "") +
                    `\n污染是历史 formal@降线/震荡 soft 残留；新 run 已被契约层拦截`
                  }
                  style={{
                    color:
                      winRate.clean_net_profit_loss_ratio >= 2.0
                        ? "var(--market-up)"
                        : winRate.clean_net_profit_loss_ratio >= 1.4
                          ? "var(--warning)"
                          : "var(--market-down)",
                    fontWeight: 500,
                  }}
                >
                  {winRate.clean_net_profit_loss_ratio.toFixed(2)}
                </strong>
              </>
            )}
        </>
      )}

      {/* AI 审查层健康度（北极星 B 项可见性缺口修复）：以最近成功审查时间为新鲜度代理，
          key 失效 / provider 故障时审查静默停摆 → 推荐缺 LLM delta 而用户无感知，此处使其可见 */}
      {(() => {
        const st = llmHealth?.status ?? null;
        if (!st) return null;
        const last = llmHealth?.last_review_at ? new Date(llmHealth.last_review_at) : null;
        const hoursAgo = last ? Math.max(0, Math.round((Date.now() - last.getTime()) / 3600000)) : null;
        const color = st === "ok" ? "var(--market-up)" : st === "stale" ? "var(--warning)" : "var(--market-down)";
        const label =
          st === "never" ? "从未审查"
          : st === "stale" ? `静默 ${hoursAgo ?? "?"}h`
          : last ? `${last.getHours().toString().padStart(2, "0")}:${last.getMinutes().toString().padStart(2, "0")} 更新` : "—";
        const title =
          `AI 审查层状态：${st}\n` +
          `最近成功审查：${llmHealth?.last_review_at ?? "无"}\n` +
          `近 24h 审查次数：${llmHealth?.recent_count_24h ?? 0}\n` +
          `缓存总数：${llmHealth?.total_entries ?? 0}\n` +
          `模型：${llmHealth?.model ?? "—"}\n` +
          (st !== "ok" ? "\n⚠️ 审查层可能已停摆（key 失效 / provider 故障），推荐或缺失 LLM delta" : "");
        return (
          <>
            <Sep />
            <span className="text-muted">AI 审查：</span>
            <strong
              className="text-mono"
              title={title}
              style={{ color, display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}
            >
              <Brain size={13} style={{ color, flexShrink: 0 }} />
              {label}
            </strong>
          </>
        );
      })()}

      {/* 右侧：数据健康度指示器（弹性推到最右） */}
      <div style={{ marginLeft: "auto", flexShrink: 0 }}>
        <button
          onClick={onViewHealthDetail}
          className="btn"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-2)",
            padding: "4px 12px",
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
            color: "var(--ink)",
            cursor: "pointer",
            transition: "all 150ms ease",
          }}
          title={issues.length > 0 ? `数据健康：共 ${issues.length} 个问题 (${highIssues.length} 高危)` : "数据健康度"}
        >
          {/* 脉冲动画：有高危问题时 */}
          {highIssues.length > 0 && (
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--market-up)",
                display: "inline-block",
                animation: "health-pulse 1.5s ease-in-out infinite",
                flexShrink: 0,
              }}
            />
          )}
          <Database size={13} style={{ color: scoreColor, flexShrink: 0 }} />
          {/* 类别标签 */}
          <span style={{ color: "var(--ink-secondary)", fontWeight: 600 }}>数据健康</span>
          {/* 分隔竖线 */}
          <span style={{ width: 1, height: 12, background: "var(--line-strong)", display: "inline-block" }} />
          {healthLoading ? (
            <span style={{ color: "var(--muted)" }}>诊断中…</span>
          ) : (
            <>
              <span className="text-mono" style={{ fontWeight: 700, color: scoreColor, letterSpacing: "-0.02em" }}>
                {score}
              </span>
              <span style={{ color: scoreColor, fontWeight: 600 }}>{scoreLabel}</span>
              {issues.length > 0 && (
                <span style={{ color: "var(--muted)", fontSize: 10 }}>
                  · {issues.length} 问题
                </span>
              )}
              <ChevronRight size={12} style={{ color: "var(--muted)", flexShrink: 0 }} />
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
    <Panel
      kicker="运行状态"
      title={
        <>
          <Activity size={16} />
          {hasRun ? "今日已运行" : run.status === "running" ? "运行中" : "今日未运行"}
        </>
      }
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
    </Panel>
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
    <Panel
      kicker="数据健康度"
      title={
        <>
          <Database size={16} />
          {loading ? "诊断中…" : `${score} 分 · ${scoreLabel}`}
        </>
      }
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
    </Panel>
  );
}

// ─── 龙头候选面板（视觉重心） ─────────────────────────────────
function CandidatePanel({
  items,
  outcomeMap,
  currentRun,
  marketRegime,
  onViewAll,
  onViewHistory,
}: {
  items: CockpitRecommendation[];
  outcomeMap: Record<string, OutcomeSummary>;
  currentRun?: RunProgress;
  marketRegime?: string;
  onViewAll: () => void;
  onViewHistory: () => void;
}) {
  const top = items.slice(0, 5);

  return (
    <Panel
      kicker="龙头候选 · 今日 top 5"
      title={
        <>
          <TrendingUp size={16} />
          候选 {items.length} 只
        </>
      }
      className="panel--min360"
      actions={
        <button className="btn btn--ghost" onClick={onViewAll}>
          查看全部
          <ChevronRight size={14} />
        </button>
      }
    >
      {top.length === 0 ? (
        <CandidateEmptyState
          currentRun={currentRun}
          marketRegime={marketRegime}
          onViewAll={onViewAll}
          onViewHistory={onViewHistory}
        />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "var(--space-3)" }}>
          {top.map((c, i) => (
            <CandidateCard key={c.recommendation_id} item={c} rank={i + 1} outcome={outcomeMap[c.code]} />
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
    </Panel>
  );
}

// 0 候选空状态：按系统运行状态区分「保护性强风控拦截」与「运行异常/未运行」，
// 避免用户把正确的动态分数线拦截误读为系统故障（北极星 A 项体验缺口）。
function CandidateEmptyState({
  currentRun,
  marketRegime,
  onViewAll,
  onViewHistory,
}: {
  currentRun?: RunProgress;
  marketRegime?: string;
  onViewAll: () => void;
  onViewHistory: () => void;
}) {
  const status = currentRun?.status;
  const regime = marketRegime?.trim();

  // 运行已完成但 0 候选 = 动态分数线主动拦截（保护准确性），非故障
  if (status === "completed") {
    const reason = regime
      ? `市场处于【${regime}】阶段，动态分数线已按当前市况校准，今日无个股达到推荐阈值（保护推荐准确性）。系统运行正常，非故障。`
      : "动态分数线已按当前市况校准，今日无个股达到推荐阈值（保护推荐准确性）。系统运行正常，非故障。";
    return (
      <EmptyState
        icon={<ShieldCheck size={28} />}
        title="今日无达标推荐"
        reason={reason}
        action={
          <button className="btn btn--ghost" onClick={onViewHistory}>
            查看运行复盘
            <ChevronRight size={14} />
          </button>
        }
      />
    );
  }

  // 运行异常（失败 / 被打断）
  if (status === "failed" || status === "interrupted") {
    return (
      <EmptyState
        icon={<AlertTriangle size={28} />}
        title="今日推荐运行异常"
        reason="最近一次推荐运行未正常完成，可稍后重试或查看运行复盘。"
        action={
          <button className="btn btn--ghost" onClick={onViewHistory}>
            查看运行复盘
            <ChevronRight size={14} />
          </button>
        }
      />
    );
  }

  // 运行中 / 尚未运行
  const running = status === "running";
  return (
    <EmptyState
      icon={<AlertTriangle size={28} />}
      title={running ? "推荐运行中" : "今日尚未运行推荐"}
      reason={running ? "正在扫描候选，请稍候刷新。" : "运行推荐后这里会展示今日达标龙头候选。"}
      action={
        <button className="btn btn--primary" onClick={onViewAll}>
          去候选池
        </button>
      }
    />
  );
}

function CandidateCard({
  item,
  rank,
  outcome,
}: {
  item: CockpitRecommendation;
  rank: number;
  outcome?: OutcomeSummary;
}) {
  const riskTone =
    item.risk_level === "high"
      ? "var(--market-up)"
      : item.risk_level === "medium"
      ? "var(--warning)"
      : "var(--market-down)";
  const toneClass =
    item.risk_level === "high" ? "tag--negative" : item.risk_level === "medium" ? "tag--warning" : "tag--positive";
  // 历史已结算推荐胜率（B 项）：settled>=2 才显示，避免单样本噪声；A 股语义涨红跌绿
  const histSettled = outcome?.settled ?? 0;
  const histWr = outcome?.win_rate ?? 0;
  const histAvg = outcome?.avg_return ?? 0;
  const showHist = histSettled >= 2;
  const histTone = histWr >= 55 ? "var(--market-up)" : histWr >= 45 ? "var(--text-muted)" : "var(--market-down)";

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
        {showHist && (
          <span
            className="text-mono"
            title={`该股历史已结算推荐 ${histSettled} 笔：胜率 ${histWr}%，均收益 ${histAvg}%`}
            style={{
              fontSize: "var(--text-xs)",
              fontWeight: 600,
              color: histTone,
              padding: "2px 6px",
              borderRadius: "var(--radius-sm, 4px)",
              border: `1px solid ${histTone}40`,
              whiteSpace: "nowrap",
            }}
          >
            史{histWr}%
          </span>
        )}
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

      {item.invalid_rule ? (
        <div
          title={item.invalid_rule}
          style={{
            fontSize: 10,
            lineHeight: 1.3,
            color: "var(--market-down)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            opacity: 0.75,
          }}
        >
          失效 · {item.invalid_rule}
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
