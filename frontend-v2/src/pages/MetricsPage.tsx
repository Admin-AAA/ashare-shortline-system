import { useState, useCallback } from "react";
import { createPortal } from "react-dom";
import { useApi } from "../app/useApi";
import { api } from "../api/client";
import type {
  AlphaDecayResponse,
  DateRangeMeta,
  DDiagnostics,
  FilterEffectiveness,
  LlmReviewHealth,
  MetricsOverview,
  PrecisionAtKRow,
  ScoreLineStatsRow,
  StrategyStatsRow,
  SetupStatsRow,
  TemporalConsistency,
} from "../types";
import type { ReactNode } from "react";
import { PageSkeleton, ErrorPage, EmptyState, CardSkeleton } from "../components/StateViews";
import { SkillsCatalogPanel } from "../components/SkillsCatalogPanel";
import { Panel } from "../components/Panel";
import { LineChart, Info } from "lucide-react";

/** 时间窗口选项：null=全部历史累计，N=最近 N 天且已结算。 */
const WINDOW_OPTIONS: Array<{ label: string; value: number | null }> = [
  { label: "全部历史", value: null },
  { label: "近 30 天", value: 30 },
  { label: "近 90 天", value: 90 },
];

/** 英文因子名 → 中文小字翻译（Alpha 衰减面板的因子列）。 */
const FACTOR_LABELS_ZH: Record<string, string> = {
  is_main_theme: "主线题材",
  breakout60: "60日突破",
  capital_resonance_signal: "资金共振信号",
  volume_breakout_signal: "量能突破信号",
  breakout20: "20日突破",
  support_ma5: "MA5 支撑",
  support_ma10: "MA10 支撑",
  is_shortline_leader_candidate: "短线龙头候选",
  is_20cm_stock: "20cm 高波动标的",
  chip_health_score_high: "筹码健康度高",
  capital_flow_score_high: "资金流分高",
};

/** 英文过滤规则名 → 中文小字翻译（过滤效果面板的规则列）。 */
const RULE_LABELS_ZH: Record<string, string> = {
  pos20_low: "获利盘 < 50%",
  avg_cost_high: "平均成本 > 200",
  regime_ma5: "结构性强势 × MA5",
};

/** 渲染英文术语 + 中文小字：英文原值下方跟一行灰色翻译，无映射时只显示原值。 */
function TermWithZh({ en, zhMap }: { en: string; zhMap: Record<string, string> }) {
  const zh = zhMap[en];
  return (
    <div>
      <div>{en}</div>
      {zh && <div style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400, marginTop: 2 }}>{zh}</div>}
    </div>
  );
}

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
    () => api.metricsByScoreLine(windowDays ?? undefined),
    [windowDays],
  );
  const { data: strategies, loading: l2, error: e2 } = useApi<StrategyStatsRow[]>(
    () => api.metricsByStrategy(windowDays ?? undefined),
    [windowDays],
  );
  const { data: setups, loading: l3, error: e3 } = useApi<SetupStatsRow[]>(
    () => api.metricsBySetup(windowDays ?? undefined),
    [windowDays],
  );
  const { data: precisionAtK, loading: l4, error: e4 } = useApi<PrecisionAtKRow[]>(
    () => api.precisionAtK(windowDays ?? undefined),
    [windowDays],
  );
  const { data: filterEffect, loading: l5, error: e5 } = useApi<FilterEffectiveness>(
    () => api.filterEffectiveness(windowDays ?? undefined),
    [windowDays],
  );
  const { data: overview, loading: l6, error: e6 } = useApi<MetricsOverview>(
    () => api.metricsOverview(windowDays ?? undefined),
    [windowDays],
  );
  const { data: alphaDecay, loading: l7, error: e7 } = useApi<AlphaDecayResponse>(
    () => api.statsAlphaDecay(),
    [],
  );

  const loading = l1 || l2 || l3 || l4 || l5 || l6 || l7;
  const error = e1 || e2 || e3 || e4 || e5 || e6 || e7;

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
      <SystemHealthPanel />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
          gap: "var(--space-4)",
        }}
      >
        <PrecisionPanel rows={precisionAtK ?? []} />
        <FilterEffectivenessPanel data={filterEffect} />
        <ScoreLinePanel rows={scoreLines ?? []} />
        <StrategyPanel rows={strategies ?? []} />
        <SetupPanel rows={setups ?? []} />
        <AlphaDecayPanel data={alphaDecay} />
        <SkillsCatalogPanel />
      </div>
    </div>
  );
}

/** 指标页通用可折叠卡片：标题 kicker/title 垂直排列，右上角带折叠按钮。 */
function MetricCard({
  kicker,
  title,
  actions,
  children,
}: {
  kicker?: ReactNode;
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Panel
      kicker={kicker}
      title={title}
      actions={actions}
    >
      {children}
    </Panel>
  );
}

/** 把 ISO 时间转成「X 分钟/小时/天前」，null/无效返回「从未」。 */
function relTime(iso: string | null | undefined): string {
  if (!iso) return "从未";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "未知";
  const diffMs = Date.now() - t;
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  return `${day} 天前`;
}

/** 系统可信度面板 —— 后端诊断一屏可见：
 *  - LLM 审查层新鲜度 / 时态一致性（B/C）
 *  - D 项真残留毒区 + 伪活跃反事实（与 self_check 同源，T32）
 * 各子视图独立加载 / 容错。 */
function SystemHealthPanel() {
  const { data: review, loading: lR, error: eR } = useApi<LlmReviewHealth>(
    () => api.llmReviewHealth(),
    [],
  );
  const { data: temporal, loading: lT, error: eT } = useApi<TemporalConsistency>(
    () => api.temporalConsistency(),
    [],
  );
  const { data: dDiag, loading: lD, error: eD } = useApi<DDiagnostics>(
    () => api.dDiagnostics(),
    [],
  );

  return (
    <MetricCard
      kicker="系统可信度"
      title="审查层 · 一致性 · D 项毒区"
      actions={
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
          北极星 B/C/D 可观测
        </span>
      }
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: "var(--space-4)",
        }}
      >
        <ReviewHealthBlock data={review} loading={lR} error={eR} />
        <TemporalConsistencyBlock data={temporal} loading={lT} error={eT} />
        <DDiagnosticsBlock data={dDiag} loading={lD} error={eD} />
      </div>
    </MetricCard>
  );
}

const MODE_LABEL: Record<string, string> = {
  no_follow_through: "无跟风",
  extension_chase: "追高延伸",
  weak_stock: "弱势股",
  gap_roundtrip: "跳空往返",
  other: "其他",
};

/** D 项真残留毒区 + 伪活跃反事实块（T32）。 */
function DDiagnosticsBlock({
  data,
  loading,
  error,
}: {
  data: DDiagnostics | null | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <CardSkeleton count={3} />;
  if (error) return <HealthErrorNote msg="D 项诊断获取失败" />;
  if (!data) return null;

  const n = data.residual_n ?? 0;
  const pa = data.pseudo_active;
  const topMode = data.modes?.[0];
  const ok = n === 0;
  const meta = ok
    ? { label: "干净", color: "var(--market-down)" }
    : pa && pa.pseudo_n >= Math.max(3, Math.floor(pa.weak_active_n / 2))
      ? { label: "历史伪活跃", color: "var(--warning)" }
      : { label: "有残留", color: "var(--market-up)" };

  return (
    <div
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: meta.color,
            boxShadow: `0 0 0 3px color-mix(in srgb, ${meta.color} 18%, transparent)`,
          }}
        />
        <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: meta.color }}>
          D 项毒区 · {meta.label}
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        <HealthRow label="真残留" value={`${n} 笔`} />
        {topMode && (
          <HealthRow
            label="主模式"
            value={`${MODE_LABEL[topMode.mode] ?? topMode.mode} ${topMode.n}·均${topMode.avg_return}%`}
          />
        )}
        {pa && pa.weak_active_n > 0 ? (
          <HealthRow
            label="弱活伪活跃"
            value={`${pa.pseudo_n}/${pa.weak_active_n}（仍活跃 ${pa.still_active_n}）`}
          />
        ) : (
          <HealthRow label="弱活伪活跃" value="—" />
        )}
        {pa?.note && (
          <div
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--text-muted)",
              lineHeight: 1.45,
              marginTop: 2,
            }}
            title={pa.note}
          >
            {pa.note.length > 72 ? `${pa.note.slice(0, 72)}…` : pa.note}
          </div>
        )}
      </div>
    </div>
  );
}

/** 审查层新鲜度块：状态徽章 + 最近审查时间 + 24h 次数 + 模型。 */
function ReviewHealthBlock({
  data,
  loading,
  error,
}: {
  data: LlmReviewHealth | null | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <CardSkeleton count={3} />;
  if (error) return <HealthErrorNote msg="审查层状态获取失败" />;
  if (!data) return null;

  const meta =
    data.status === "ok"
      ? { label: "正常", color: "var(--market-down)" }
      : data.status === "stale"
        ? { label: "陈旧", color: "var(--warning)" }
        : { label: "从未", color: "var(--market-up)" };

  return (
    <div
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: meta.color,
            boxShadow: `0 0 0 3px color-mix(in srgb, ${meta.color} 18%, transparent)`,
          }}
        />
        <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: meta.color }}>AI 审查层 · {meta.label}</span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        <HealthRow label="最近审查" value={relTime(data.last_review_at)} />
        <HealthRow label="近 24h 次数" value={String(data.recent_count_24h)} />
        <HealthRow label="缓存总数" value={String(data.total_entries)} />
        <HealthRow label="模型" value={data.model ?? "—"} />
      </div>
    </div>
  );
}

/** 时态一致性块：状态 + 通过/警告/失败计数 + 违规数。 */
function TemporalConsistencyBlock({
  data,
  loading,
  error,
}: {
  data: TemporalConsistency | null | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <CardSkeleton count={3} />;
  if (error) return <HealthErrorNote msg="一致性检查获取失败" />;
  if (!data) return null;

  const summary = data.check_summary ?? {};
  const failed = summary.failed ?? 0;
  const warnings = summary.warnings ?? 0;
  const passed = summary.passed ?? 0;

  const ok = data.status === "completed" && failed === 0;
  const meta = ok
    ? { label: "一致", color: "var(--market-down)" }
    : data.status === "no_data"
      ? { label: "无数据", color: "var(--text-muted)" }
      : { label: failed > 0 ? "违规" : "告警", color: failed > 0 ? "var(--market-up)" : "var(--warning)" };

  return (
    <div
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: meta.color,
            boxShadow: `0 0 0 3px color-mix(in srgb, ${meta.color} 18%, transparent)`,
          }}
        />
        <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: meta.color }}>时态一致性 · {meta.label}</span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        <HealthRow label="检查样本" value={`${data.recommendation_count ?? 0} 笔 / ${data.run_count ?? 0} run`} />
        <HealthRow label="通过 / 告警 / 失败" value={`${passed} / ${warnings} / ${failed}`} />
        <HealthRow
          label="未来引用告警"
          value={String(warnings)}
          color={warnings > 0 ? "var(--warning)" : undefined}
        />
      </div>
    </div>
  );
}

/** 单条指标：左标签右等宽数字（与全局「数字右对齐 + 等宽」一致）。 */
function HealthRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--space-3)" }}>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{label}</span>
      <span
        style={{
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "var(--text-sm)",
          fontWeight: 600,
          color: color ?? "var(--ink)",
          textAlign: "right",
        }}
      >
        {value}
      </span>
    </div>
  );
}

/** 子块加载/获取失败时的内联降级提示（不依赖整页 ErrorPage）。 */
function HealthErrorNote({ msg }: { msg: string }) {
  return (
    <div
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
        fontSize: "var(--text-xs)",
        color: "var(--text-muted)",
      }}
    >
      {msg}
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
            <>统计范围：全部历史累计推荐（无时间窗口）</>
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
    <MetricCard
      kicker="推荐质量概览"
      title="核心 KPI"
      actions={
        data.sample_confidence === "low" ? (
          <span className="tag tag--warning">样本不足</span>
        ) : undefined
      }
    >
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
    </MetricCard>
  );
}

/** Alpha 衰减面板 —— 对比因子早期 vs 近期的 point-biserial 相关性 + 胜率边际。
 * 后端 /api/stats/alpha-decay 早已返回，前端无类型无页面——本轮补齐。
 * 衰变中(decaying)红 / 改善中(improving)绿 / 稳定(stable)灰。 */
function AlphaDecayPanel({ data }: { data: AlphaDecayResponse | null | undefined }) {
  if (!data || !data.factors || data.factors.length === 0) return null;
  const sorted = [...data.factors].sort((a, b) => a.delta - b.delta); // delta 升序（最衰变在前）

  const statusColor = (s: string) =>
    s === "decaying" ? "var(--market-down)" : s === "improving" ? "var(--market-up)" : "var(--text-muted)";
  const statusLabel = (s: string) =>
    s === "decaying" ? "衰变中" : s === "improving" ? "改善中" : "稳定";

  return (
    <MetricCard
      kicker="因子 Alpha 衰减检测"
      title="早期 vs 近期"
      actions={
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
          {data.early_period} → {data.recent_period}
        </span>
      }
    >
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-xs)" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--text-muted)", textAlign: "right" }}>
              <th style={{ textAlign: "left", padding: "var(--space-2)", fontWeight: 600 }}>因子</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>状态</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>早期 r</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>近期 r</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>Δ</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>衰减%</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>早期胜率边际</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>近期胜率边际</th>
              <th style={{ padding: "var(--space-2)", fontWeight: 600 }}>样本</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((f) => (
              <tr key={f.factor} style={{ borderBottom: "1px solid var(--line)", textAlign: "right" }}>
                <td style={{ textAlign: "left", padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: "var(--ink)" }}>
                  <TermWithZh en={f.factor} zhMap={FACTOR_LABELS_ZH} />
                </td>
                <td style={{ padding: "var(--space-2)" }}>
                  <span style={{ color: statusColor(f.status), fontWeight: 600 }}>{statusLabel(f.status)}</span>
                </td>
                <td style={{ padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: f.early_r >= 0 ? "var(--market-up)" : "var(--market-down)" }}>
                  {f.early_r.toFixed(3)}
                </td>
                <td style={{ padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: f.recent_r >= 0 ? "var(--market-up)" : "var(--market-down)" }}>
                  {f.recent_r.toFixed(3)}
                </td>
                <td style={{ padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: f.delta >= 0 ? "var(--market-up)" : "var(--market-down)" }}>
                  {f.delta >= 0 ? "+" : ""}{f.delta.toFixed(3)}
                </td>
                <td style={{ padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: statusColor(f.status) }}>
                  {f.decay_pct >= 0 ? "+" : ""}{f.decay_pct.toFixed(0)}%
                </td>
                <td style={{ padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: "var(--ink-secondary)" }}>
                  {f.early_win_edge.toFixed(1)}
                </td>
                <td style={{ padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: "var(--ink-secondary)" }}>
                  {f.recent_win_edge.toFixed(1)}
                </td>
                <td style={{ padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                  {f.early_true_n}/{f.recent_true_n}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginTop: "var(--space-2)" }}>
        r = point-biserial 相关性（因子=true vs =false 的胜率差）；胜率边际 = 因子为真时的胜率 - 因子为假时的胜率。衰变中=近期 r 显著低于早期。
      </div>
    </MetricCard>
  );
}

// 历史过滤规则元信息：deprecated 规则已从实盘策略删除（见 strategy.yaml），
// 此处仅作复盘 + 回归护栏。注意：误杀（被砍胜率≥保留）是"有害"信号。
function PrecisionPanel({ rows }: { rows: PrecisionAtKRow[] }) {
  const [tip, setTip] = useState<{ show: boolean; x: number; y: number }>({ show: false, x: 0, y: 0 });

  const onEnter = useCallback((e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setTip({ show: true, x: rect.left + rect.width / 2, y: rect.bottom + 8 });
  }, []);
  const onLeave = useCallback(() => setTip(t => ({ ...t, show: false })), []);

  if (rows.length === 0) return null;
  return (
    <MetricCard
      kicker="排序质量"
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          Precision@K
          <span
            style={{ display: "inline-flex", cursor: "help", color: "var(--text-muted)" }}
            onMouseEnter={onEnter}
            onMouseLeave={onLeave}
            onClick={(e) => e.stopPropagation()}
          >
            <Info size={14} />
          </span>
        </span>
      }
    >
      {tip.show && createPortal(
        <div
          style={{
            position: "fixed",
            top: tip.y,
            left: tip.x,
            transform: "translateX(-50%)",
            width: 320,
            padding: "12px 16px",
            background: "#252540",
            border: "1px solid #555",
            borderRadius: 8,
            fontSize: 13,
            lineHeight: 1.7,
            color: "#d0d0d0",
            whiteSpace: "normal",
            zIndex: 99999,
            boxShadow: "0 8px 24px rgba(0,0,0,.6)",
            pointerEvents: "none",
          }}
        >
          <strong style={{ color: "#fff", display: "block", marginBottom: 6 }}>
            Precision@K — 前 K 推荐准确率
          </strong>
          系统每次会推荐一组股票并按好坏排序。<br />
          「前 K」表示取排名最靠前的 K 只来看，「命中率」是其中真正赚钱的比例。<br />
          <span style={{ color: "#aaa", display: "block", marginTop: 6 }}>
            例：前1 = 60% → 每次推荐的第1只股票，有60%概率是涨的。<br />
            数值越高且从前1到前10递减越慢，说明排序越准。
          </span>
        </div>,
        document.body,
      )}
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
    </MetricCard>
  );
}

function ScoreLinePanel({ rows }: { rows: ScoreLineStatsRow[] }) {
  if (rows.length === 0) return null;
  return (
    <MetricCard
      kicker="分析"
      title="分数段胜率"
      actions={
        <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
          推荐分数线 ≥ X 的累计胜率
        </span>
      }
    >
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
    </MetricCard>
  );
}

function StrategyPanel({ rows }: { rows: StrategyStatsRow[] }) {
  if (rows.length === 0) return null;
  const sorted = [...rows].sort((a, b) => b.hit_rate - a.hit_rate);
  return (
    <MetricCard
      kicker="分析"
      title="策略胜率"
      actions={
        <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
          按胜率降序
        </span>
      }
    >
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
    </MetricCard>
  );
}

function SetupPanel({ rows }: { rows: SetupStatsRow[] }) {
  if (rows.length === 0) return null;
  const sorted = [...rows].sort((a, b) => b.hit_rate - a.hit_rate);
  return (
    <MetricCard
      kicker="分析"
      title="Setup 胜率"
      actions={
        <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
          按胜率降序
        </span>
      }
    >
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
    </MetricCard>
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

  const keptWr = data.kept.net_win_rate;
  const keptWrColor =
    keptWr >= 60 ? "var(--color-up)" : keptWr >= 45 ? "var(--text-secondary)" : "var(--color-down)";
  const keptRet = data.kept.net_avg_return;
  const keptPf = data.kept.net_profit_factor;

  return (
    <MetricCard
      kicker="分析"
      title="过滤效果"
      actions={
        <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
          过滤率 {data.filter_rate_pct.toFixed(1)}%
        </span>
      }
    >
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
              <div style={{ fontWeight: 600, color: "var(--ink)" }}>
                <TermWithZh en={r.rule} zhMap={RULE_LABELS_ZH} />
              </div>
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
    </MetricCard>
  );
}
