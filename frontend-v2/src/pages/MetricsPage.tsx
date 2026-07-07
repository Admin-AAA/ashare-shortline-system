import { useState } from "react";
import { useApi } from "../app/useApi";
import { api } from "../api/client";
import type {
  DateRangeMeta,
  FilterEffectiveness,
  MetricsOverview,
  PrecisionAtKRow,
  ScoreLineStatsRow,
  StrategyStatsRow,
  SetupStatsRow,
} from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { SkillsCatalogPanel } from "../components/SkillsCatalogPanel";
import { LineChart } from "lucide-react";

/** 时间窗口选项：null=全部历史累计，N=最近 N 天且已结算。 */
const WINDOW_OPTIONS: Array<{ label: string; value: number | null }> = [
  { label: "全部历史", value: null },
  { label: "近 30 天", value: 30 },
  { label: "近 90 天", value: 90 },
];

/**
 * 指标页 —— 推荐质量分析
 * 后端有 /api/metrics/by_score_line, by_strategy, by_setup, precision-at-k, filter-effectiveness 五个端点。
 *
 * 五视图：
 * 1. Precision@K：前 K 推荐准确率，衡量推荐排序质量（越靠前应越高）
 * 2. 过滤效果：各过滤规则保留/过滤的胜率对比，暴露误杀
 * 3. 分数段胜率：哪个分数线以上的推荐质量最高
 * 4. 策略胜率：哪个策略表现最好/最差
 * 5. Setup 胜率：哪个 setup 类型最有效
 */
export function MetricsPage() {
  const [windowDays, setWindowDays] = useState<number | null>(null);

  const { data: scoreLines, loading: l1, error: e1 } = useApi<ScoreLineStatsRow[]>(
    () => api.metricsByScoreLine("formal", windowDays ?? undefined),
    [windowDays],
  );
  const { data: strategies, loading: l2, error: e2 } = useApi<StrategyStatsRow[]>(
    () => api.metricsByStrategy("formal", windowDays ?? undefined),
    [windowDays],
  );
  const { data: setups, loading: l3, error: e3 } = useApi<SetupStatsRow[]>(
    () => api.metricsBySetup("formal", windowDays ?? undefined),
    [windowDays],
  );
  const { data: precisionAtK, loading: l4, error: e4 } = useApi<PrecisionAtKRow[]>(
    () => api.precisionAtK("formal", windowDays ?? undefined),
    [windowDays],
  );
  const { data: filterEffect, loading: l5, error: e5 } = useApi<FilterEffectiveness>(
    () => api.filterEffectiveness("formal", windowDays ?? undefined),
    [windowDays],
  );
  const { data: overview, loading: l6, error: e6 } = useApi<MetricsOverview>(
    () => api.metricsOverview("formal", windowDays ?? undefined),
    [windowDays],
  );

  const loading = l1 || l2 || l3 || l4 || l5 || l6;
  const error = e1 || e2 || e3 || e4 || e5 || e6;

  // 从任一响应取 date_range 元信息（优先 filterEffect，其次 scoreLines/strategies 首项）
  const dateRange: DateRangeMeta | undefined =
    filterEffect?.date_range
    ?? scoreLines?.[0]?.date_range
    ?? strategies?.[0]?.date_range
    ?? precisionAtK?.[0]?.date_range
    ?? setups?.[0]?.date_range;

  if (loading) return <PageSkeleton title="指标分析" hint="加载推荐质量数据" rowCount={6} />;
  if (error) return <ErrorPage title="指标加载失败" message={error} />;
  if ((!scoreLines || scoreLines.length === 0) && (!strategies || strategies.length === 0))
    return <EmptyState icon={<LineChart size={32} />} title="暂无指标数据" reason="还没有足够的推荐 outcome" />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <MetricsWindowBar
        windowDays={windowDays}
        onSelect={setWindowDays}
        dateRange={dateRange}
      />
      <OverviewKpiPanel data={overview} />
      <PrecisionPanel rows={precisionAtK ?? []} />
      <FilterEffectivenessPanel data={filterEffect} />
      <ScoreLinePanel rows={scoreLines ?? []} />
      <StrategyPanel rows={strategies ?? []} />
      <SetupPanel rows={setups ?? []} />
      <SkillsCatalogPanel />
    </div>
  );
}

/** 指标页顶部的时间窗口选择器 + 结算截止说明。 */
function MetricsWindowBar({
  windowDays,
  onSelect,
  dateRange,
}: {
  windowDays: number | null;
  onSelect: (v: number | null) => void;
  dateRange?: DateRangeMeta;
}) {
  return (
    <div className="panel" style={{ padding: "var(--space-3) var(--space-4)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "var(--text-muted)", fontWeight: 600 }}>时间窗口</span>
        <div style={{ display: "flex", gap: "var(--space-1)" }}>
          {WINDOW_OPTIONS.map((opt) => {
            const active = windowDays === opt.value;
            return (
              <button
                key={String(opt.value)}
                onClick={() => onSelect(opt.value)}
                style={{
                  padding: "4px 12px",
                  fontSize: 13,
                  borderRadius: 4,
                  border: "1px solid var(--border)",
                  background: active ? "var(--color-accent, #2563eb)" : "var(--surface-1)",
                  color: active ? "#fff" : "var(--text-secondary)",
                  cursor: "pointer",
                  fontWeight: active ? 600 : 400,
                }}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-muted)" }}>
          {dateRange && dateRange.window_days ? (
            <>
              窗口 {dateRange.start_date} ~ {dateRange.end_date}（已结算截止 {dateRange.settle_cutoff}，
              排除未结算 {dateRange.pending_excluded} 笔，已结算 {dateRange.labeled_in_window} 笔）
            </>
          ) : (
            <>统计范围：formal 模式全部历史累计推荐（无时间窗口）</>
          )}
        </span>
      </div>
    </div>
  );
}

/** 顶部 KPI 概览 —— 北极星 D 项核心监控指标一屏可见。
 * 后端 /api/metrics/overview 早已返回，前端类型+client 也齐备，但页面没渲染——本轮补齐。 */
function OverviewKpiPanel({ data }: { data: MetricsOverview | null | undefined }) {
  if (!data) return null;
  // 关键 KPI：胜率/盈亏比/平均收益/最大回撤/超额收益/触发率/样本/MFE-MAE/持仓天数
  const kpis = [
    {
      label: "胜率",
      value: data.hit_rate != null ? `${(data.hit_rate * 100).toFixed(1)}%` : "—",
      tone: data.hit_rate >= 0.6 ? "up" : data.hit_rate >= 0.5 ? "neutral" : "down",
      sub: `${data.success_count}/${data.labeled_count} 笔`,
    },
    {
      label: "盈亏比",
      value: data.profit_loss_ratio != null ? data.profit_loss_ratio.toFixed(2) : "—",
      tone: data.profit_loss_ratio >= 1.4 ? "up" : "neutral",
      sub: "win/loss",
    },
    {
      label: "平均收益",
      value: data.average_net_return != null ? `${data.average_net_return.toFixed(2)}%` : "—",
      tone: data.average_net_return >= 0 ? "up" : "down",
      sub: `中位 ${(data.median_net_return ?? 0).toFixed(2)}%`,
    },
    {
      label: "最大亏损",
      value: data.max_drawdown != null ? `${data.max_drawdown.toFixed(2)}%` : "—",
      tone: "down",
      sub: "单笔最差",
    },
    {
      label: "超额收益",
      value: data.benchmark_excess_return != null ? `${data.benchmark_excess_return.toFixed(1)}%` : "—",
      tone: data.benchmark_excess_return >= 0 ? "up" : "down",
      sub: "vs 基准",
    },
    {
      label: "触发率",
      value: data.trigger_rate != null ? `${(data.trigger_rate * 100).toFixed(1)}%` : "—",
      tone: "neutral",
      sub: `${data.executed_trade_count}/${data.total_recommendations}`,
    },
    {
      label: "MFE / MAE",
      value: `${(data.avg_mfe ?? 0).toFixed(1)} / ${(data.avg_mae ?? 0).toFixed(1)}`,
      tone: "neutral",
      sub: "平均浮盈/浮亏",
    },
    {
      label: "持仓天数",
      value: data.avg_holding_days != null ? data.avg_holding_days.toFixed(1) : "—",
      tone: "neutral",
      sub: "平均",
    },
  ];

  return (
    <div className="panel" style={{ padding: "var(--space-4)" }}>
      <div className="panel__header" style={{ marginBottom: "var(--space-3)" }}>
        <div>
          <div className="panel__kicker">推荐质量概览</div>
          <h3 className="panel__title">核心 KPI</h3>
        </div>
        {data.sample_confidence === "low" && (
          <span className="tag tag--warning">样本不足</span>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "var(--space-3)" }}>
        {kpis.map((k) => (
          <div
            key={k.label}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--line)",
              borderRadius: "var(--radius-sm, 6px)",
              padding: "var(--space-3)",
            }}
          >
            <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginBottom: "var(--space-1)" }}>
              {k.label}
            </div>
            <div
              style={{
                fontFamily: "var(--font-mono, monospace)",
                fontSize: "var(--text-lg)",
                fontWeight: 600,
                color:
                  k.tone === "up"
                    ? "var(--market-up)"
                    : k.tone === "down"
                      ? "var(--market-down)"
                      : "var(--ink)",
              }}
            >
              {k.value}
            </div>
            <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginTop: 2 }}>
              {k.sub}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// 历史过滤规则元信息：deprecated 规则已从实盘策略删除（见 strategy.yaml），
// 此处仅作复盘 + 回归护栏。注意：误杀（被砍胜率≥保留）是"有害"信号。
function PrecisionPanel({ rows }: { rows: PrecisionAtKRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">排序质量</span>
        <h2 className="panel__title">Precision@K</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          前 K 推荐准确率（越靠前应越高）
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {rows.map((r) => {
          const pct = r.precision * 100;
          const color = pct >= 60 ? "var(--color-up)" : pct >= 45 ? "var(--text-secondary)" : "var(--color-down)";
          return (
            <div
              key={r.k}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr auto auto",
                gap: "var(--space-4)",
                alignItems: "center",
                padding: "var(--space-2) var(--space-4)",
                background: "var(--surface-1)",
                borderRadius: 6,
              }}
            >
              <div style={{ fontWeight: 600, color: "var(--ink)" }}>前 {r.k}</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {r.success_count}/{r.evaluated_count} 笔命中 · {r.sample_runs} 个 run
              </div>
              <div style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 18, fontWeight: 600, color }}>
                {pct.toFixed(1)}%
              </div>
              <div
                style={{
                  width: 120,
                  height: 6,
                  background: "var(--surface-2)",
                  borderRadius: 3,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{ width: `${Math.min(100, pct)}%`, height: "100%", background: color, borderRadius: 3 }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ScoreLinePanel({ rows }: { rows: ScoreLineStatsRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">分析</span>
        <h2 className="panel__title">分数段胜率</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          推荐分数线 ≥ X 的累计胜率
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {rows.map((r) => (
          <StatsRow
            key={r.score_line}
            label={`≥ ${r.score_line.toFixed(0)}`}
            sample={r.sample_count}
            hitRate={r.hit_rate}
            avgReturn={r.avg_return}
            avgDrawdown={r.avg_drawdown}
          />
        ))}
      </div>
    </div>
  );
}

function StrategyPanel({ rows }: { rows: StrategyStatsRow[] }) {
  if (rows.length === 0) return null;
  const sorted = [...rows].sort((a, b) => b.hit_rate - a.hit_rate);
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">分析</span>
        <h2 className="panel__title">策略胜率</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          按胜率降序
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {sorted.map((r) => (
          <StatsRow
            key={r.strategy_id}
            label={r.strategy_name || r.strategy_id}
            sample={r.sample_count}
            hitRate={r.hit_rate}
            avgReturn={r.avg_return}
          />
        ))}
      </div>
    </div>
  );
}

function SetupPanel({ rows }: { rows: SetupStatsRow[] }) {
  if (rows.length === 0) return null;
  const sorted = [...rows].sort((a, b) => b.hit_rate - a.hit_rate);
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">分析</span>
        <h2 className="panel__title">Setup 胜率</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          按胜率降序
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {sorted.map((r) => (
          <StatsRow
            key={r.setup_type}
            label={r.setup_type}
            sample={r.sample_count}
            hitRate={r.hit_rate}
            avgReturn={r.avg_return}
          />
        ))}
      </div>
    </div>
  );
}

function StatsRow({
  label,
  sample,
  hitRate,
  avgReturn,
  avgDrawdown,
}: {
  label: string;
  sample: number;
  hitRate: number;
  avgReturn: number;
  avgDrawdown?: number;
}) {
  const wr = hitRate * 100;
  const wrColor = wr >= 60 ? "var(--color-up)" : wr >= 45 ? "var(--text-secondary)" : "var(--color-down)";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto auto auto auto",
        gap: "var(--space-4)",
        alignItems: "center",
        padding: "var(--space-2) var(--space-4)",
        background: "var(--surface-1)",
        borderRadius: 6,
      }}
    >
      <div style={{ fontWeight: 600, color: "var(--ink)" }}>{label}</div>
      <Metric label="样本" value={String(sample)} />
      <Metric label="胜率" value={`${wr.toFixed(1)}%`} color={wrColor} />
      <Metric
        label="平均"
        value={`${avgReturn >= 0 ? "+" : ""}${avgReturn.toFixed(2)}%`}
        color={avgReturn >= 0 ? "var(--color-up)" : "var(--color-down)"}
      />
      {avgDrawdown !== undefined && (
        <Metric label="回撤" value={`${avgDrawdown.toFixed(2)}%`} color="var(--text-muted)" />
      )}
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ textAlign: "right" }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{label}</div>
      <div
        style={{
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "var(--text-md)",
          fontWeight: 600,
          color: color || "var(--text-primary)",
        }}
      >
        {value}
      </div>
    </div>
  );
}

/** 过滤效果：暴露各过滤规则是否误杀好票（胜率越高越可能误杀）。 */
function FilterEffectivenessPanel({ data }: { data: FilterEffectiveness | null | undefined }) {
  if (!data) return null;
  const rules = Object.entries(data.filtered_by_rule)
    .map(([rule, v]) => ({ rule, ...v }))
    .sort((a, b) => b.count - a.count);

  const keptWr = data.kept.net_win_rate * 100;
  const keptWrColor =
    keptWr >= 60 ? "var(--color-up)" : keptWr >= 45 ? "var(--text-secondary)" : "var(--color-down)";
  const keptRet = data.kept.net_avg_return;
  const keptPf = data.kept.net_profit_factor;

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">分析</span>
        <h2 className="panel__title">过滤效果</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          过滤率 {data.filter_rate_pct.toFixed(1)}%
        </span>
      </div>

      {/* 总体：保留 vs 过滤 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <Metric label="总样本" value={String(data.count)} />
        <Metric label="过滤数" value={String(data.filtered_count)} color="var(--color-down)" />
        <Metric label="保留胜率" value={`${keptWr.toFixed(1)}%`} color={keptWrColor} />
      </div>

      {/* 保留样本质量 */}
      <div
        style={{
          display: "flex",
          gap: "var(--space-4)",
          fontSize: 13,
          color: "var(--text-muted)",
          marginBottom: "var(--space-3)",
        }}
      >
        <span>
          保留平均收益{" "}
          <b
            style={{
              color: keptRet >= 0 ? "var(--color-up)" : "var(--color-down)",
              fontFamily: "var(--font-mono, monospace)",
            }}
          >
            {keptRet >= 0 ? "+" : ""}
            {keptRet.toFixed(2)}%
          </b>
        </span>
        <span>
          保留盈利因子{" "}
          <b style={{ color: "var(--ink)", fontFamily: "var(--font-mono, monospace)" }}>
            {keptPf.toFixed(2)}
          </b>
        </span>
      </div>

      {/* 各规则过滤明细：胜率越高说明越可能误杀好票 */}
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
        按过滤量降序 · 胜率高的规则可能误杀
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {rules.map((r) => {
          const wr = r.win_rate * 100;
          const wrColor =
            wr >= 60 ? "var(--color-up)" : wr >= 45 ? "var(--text-secondary)" : "var(--color-down)";
          return (
            <div
              key={r.rule}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto auto auto",
                gap: "var(--space-4)",
                alignItems: "center",
                padding: "var(--space-2) var(--space-4)",
                background: "var(--surface-1)",
                borderRadius: 6,
              }}
            >
              <div style={{ fontWeight: 600, color: "var(--ink)" }}>{r.rule}</div>
              <Metric label="过滤" value={String(r.count)} />
              <Metric label="胜率" value={`${wr.toFixed(1)}%`} color={wrColor} />
              <Metric
                label="平均"
                value={`${r.avg_return >= 0 ? "+" : ""}${r.avg_return.toFixed(2)}%`}
                color={r.avg_return >= 0 ? "var(--color-up)" : "var(--color-down)"}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
