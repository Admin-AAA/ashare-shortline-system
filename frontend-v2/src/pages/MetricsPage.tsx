import { isAiReviewDisabled } from "../types";
import { useState, useCallback } from "react";
import { createPortal } from "react-dom";
import { useApi } from "../app/useApi";
import { api, type MetricsSampleScope } from "../api/client";
import type {
  AlphaDecayResponse,
  DateRangeMeta,
  DDiagnostics,
  CapitalFlowFactorResponse,
  DNetBreakdown,
  FilterEffectiveness,
  HistoricalReconstructionCohort,
  LlmReviewHealth,
  LlmRouteHealthItem,
  LiveFillSplit,
  LiveFillStats,
  MetricsOverview,
  PrecisionAtKRow,
  ScoreLineStatsRow,
  StrategyStatsRow,
  StrategyListItem,
  SetupStatsRow,
  TemporalConsistency,
  RuntimeMetricsSnapshot,
  RuntimeMetricTiming,
} from "../types";
import type { ReactNode, CSSProperties } from "react";
import { PageSkeleton, ErrorPage, EmptyState, CardSkeleton } from "../components/StateViews";
import { SkillsCatalogPanel } from "../components/SkillsCatalogPanel";
import { Panel } from "../components/Panel";
import { LineChart, Info } from "lucide-react";
import { utcMs } from "../utils/time";

/** 时间窗口选项：null=全部历史累计，N=最近 N 天且已结算。 */
const WINDOW_OPTIONS: Array<{ label: string; value: number | null }> = [
  { label: "全部历史", value: null },
  { label: "近 30 天", value: 30 },
  { label: "近 90 天", value: 90 },
];
const DEFAULT_WINDOW_DAYS = 30;
const DEFAULT_SAMPLE_SCOPE: MetricsSampleScope = "live_forward";

const SAMPLE_SCOPE_OPTIONS: Array<{
  label: string;
  value: MetricsSampleScope;
}> = [
  { label: "LIVE 前瞻", value: "live_forward" },
  { label: "历史重建（研究）", value: "historical_replay" },
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
  const [windowDays, setWindowDays] = useState<number | null>(DEFAULT_WINDOW_DAYS);
  const [sampleScope, setSampleScope] = useState<MetricsSampleScope>(DEFAULT_SAMPLE_SCOPE);

  const { data: scoreLines, loading: l1, error: e1 } = useApi<ScoreLineStatsRow[]>(
    () => api.metricsByScoreLine(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );
  const { data: strategies, loading: l2, error: e2 } = useApi<StrategyStatsRow[]>(
    () => api.metricsByStrategy(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );
  const { data: setups, loading: l3, error: e3 } = useApi<SetupStatsRow[]>(
    () => api.metricsBySetup(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );
  const { data: precisionAtK, loading: l4, error: e4 } = useApi<PrecisionAtKRow[]>(
    () => api.precisionAtK(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );
  const { data: filterEffect, loading: l5, error: e5 } = useApi<FilterEffectiveness>(
    () => api.filterEffectiveness(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );
  const { data: overview, loading: l6, error: e6 } = useApi<MetricsOverview>(
    () => api.metricsOverview(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );
  const { data: alphaDecay, loading: l7, error: e7 } = useApi<AlphaDecayResponse>(
    () => api.statsAlphaDecay(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );

  // 性能：只由「概览」决定整块骨架/空态；其余 6 个面板各自到达即渲染（原先需等 7 个端点全部返回）。
  const loading = l6;
  const error = e6;

  // 概览覆盖窗口内全部推荐及 pending/missed；其余端点只作无概览时的降级来源。
  const dateRange: DateRangeMeta | undefined =
    overview?.date_range
    ?? filterEffect?.date_range
    ?? scoreLines?.[0]?.date_range
    ?? strategies?.[0]?.date_range
    ?? precisionAtK?.[0]?.date_range
    ?? setups?.[0]?.date_range;

  const hasQualityData = Boolean(overview && overview.labeled_count > 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <MetricsWindowBar
        windowDays={windowDays}
        onSelect={setWindowDays}
        sampleScope={sampleScope}
        onScopeSelect={setSampleScope}
        dateRange={dateRange}
        historicalCohort={overview?.historical_reconstruction_cohort}
      />
      <SystemHealthPanel />
      <RuntimeMetricsPanel />
      {loading ? (
        <PageSkeleton title="指标分析" hint="加载推荐质量数据" rowCount={6} />
      ) : error ? (
        <ErrorPage title="指标加载失败" message={error} />
      ) : !hasQualityData ? (
        <EmptyState
          icon={<LineChart size={32} />}
          title="当前口径暂无指标数据"
          reason={sampleScope === "live_forward"
            ? "尚无已结算的 LIVE 前瞻推荐；可切换到历史回放查看研究样本"
            : "当前窗口没有完整、同策略 policy、同 execution policy、同代码版本的历史回放 cohort"}
        />
      ) : (
        <>
          <OverviewKpiPanel data={overview} />
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
              gap: "var(--space-4)",
            }}
          >
            <PanelSlot loading={l4} error={e4} title="Precision@K"><PrecisionPanel rows={precisionAtK ?? []} /></PanelSlot>
            <PanelSlot loading={l5} error={e5} title="过滤效果"><FilterEffectivenessPanel data={filterEffect} /></PanelSlot>
            <PanelSlot loading={l1} error={e1} title="分数段胜率"><ScoreLinePanel rows={scoreLines ?? []} /></PanelSlot>
            <PanelSlot loading={l2} error={e2} title="策略胜率"><StrategyPanel rows={strategies ?? []} /></PanelSlot>
            <PanelSlot loading={l3} error={e3} title="Setup 胜率"><SetupPanel rows={setups ?? []} /></PanelSlot>
            <PanelSlot loading={l7} error={e7} title="Alpha 衰减"><AlphaDecayPanel data={alphaDecay} /></PanelSlot>
            <SkillsCatalogPanel />
          </div>
          <DNetPanel windowDays={windowDays} sampleScope={sampleScope} />
        </>
      )}
    </div>
  );
}

/** 面板占位：数据未到显示骨架、出错显示该面板错误，到达后渲染原面板（内容与计算不变）。 */
function PanelSlot({ loading, error, title, children }: { loading: boolean; error: string; title: string; children: ReactNode }) {
  if (loading) return <PageSkeleton title={title} hint="加载中" rowCount={4} />;
  if (error) return <ErrorPage title={`${title}加载失败`} message={error} />;
  return <>{children}</>;
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
  const t = utcMs(iso);
  if (t == null) return "未知";
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
 *  - D 项真残留毒区 + 伪活跃反事实 + phase 信息量（与 self_check 同源，T32/T34）
 * 各子视图独立加载 / 容错。 */
function SystemHealthPanel() {
  const { data: review, loading: lR, error: eR } = useApi<LlmReviewHealth>(
    () => api.llmReviewHealth(),
    [],
  );
  const { data: routes, loading: lRoutes, error: eRoutes } = useApi<LlmRouteHealthItem[]>(
    () => api.llmRouteHealth(),
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
  const { data: capFactor, loading: lCF, error: eCF } = useApi<CapitalFlowFactorResponse>(
    () => api.capitalFlowFactor(),
    [],
  );

  return (
    <MetricCard
      kicker="系统可信度"
      title="AI 审查 · 模型路由 · 时态一致性 · D 项毒区 · 资金流因子 · phase"
      actions={
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
          各诊断项独立加载
        </span>
      }
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
          gap: "var(--space-4)",
        }}
      >
        <ReviewHealthBlock data={review} loading={lR} error={eR} />
        <RouteHealthBlock data={routes} loading={lRoutes} error={eRoutes} />
        <TemporalConsistencyBlock data={temporal} loading={lT} error={eT} />
        <DDiagnosticsBlock data={dDiag} loading={lD} error={eD} />
        <CapitalFlowFactorBlock data={capFactor} loading={lCF} error={eCF} />
        <PhaseInformationBlock data={dDiag} loading={lD} error={eD} />
      </div>
    </MetricCard>
  );
}

const fmtSec = (s: number) => (s >= 1 ? `${s.toFixed(2)}s` : `${(s * 1000).toFixed(0)}ms`);

function groupSum<T>(items: T[], key: (t: T) => string, val: (t: T) => number): Array<[string, number]> {
  const m = new Map<string, number>();
  for (const it of items) m.set(key(it), (m.get(key(it)) ?? 0) + val(it));
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function TimingTable({ rows, labelOf }: { rows: RuntimeMetricTiming[]; labelOf: (r: RuntimeMetricTiming) => string }) {
  if (rows.length === 0) return <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>本进程启动以来暂无记录</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
      {rows.map((r) => (
        <HealthRow
          key={labelOf(r)}
          label={labelOf(r)}
          value={`${r.count} 次 · 均 ${fmtSec(r.avg_seconds)} · 最长 ${fmtSec(r.max_seconds)}`}
        />
      ))}
    </div>
  );
}

/** 运行时指标面板 —— GET /api/metrics/runtime（进程内计数，后端重启后清零）：
 *  数据源成功/失败、备用源切换、限速等待、LLM 耗时、盘后各阶段耗时。 */
function RuntimeMetricsPanel() {
  const { data, loading, error } = useApi<RuntimeMetricsSnapshot>(() => api.runtimeMetrics(), []);
  const block: CSSProperties = { padding: "var(--space-3)", border: "1px solid var(--border)", borderRadius: "var(--radius-md, 8px)" };
  const head: CSSProperties = { fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--ink)", marginBottom: "var(--space-2)" };
  let body: ReactNode;
  if (loading) body = <CardSkeleton />;
  else if (error || !data) body = <div style={{ fontSize: "var(--text-xs)", color: "var(--market-up)" }}>运行时指标加载失败：{error ?? "无数据"}</div>;
  else {
    const counters = data.counters ?? [];
    const timings = data.timings ?? [];
    const req = counters.filter((c) => c.name === "provider_requests");
    const providers = [...new Set(req.map((c) => c.labels.provider))].sort();
    const fallbacks = groupSum(counters.filter((c) => c.name === "provider_fallback"), (c) => `${c.labels.fallback_from ?? "?"} → ${c.labels.provider ?? "?"}`, (c) => c.value);
    const waits = timings.filter((t) => t.name === "provider_ratelimit_wait");
    const llm = timings.filter((t) => t.name === "llm_latency");
    const stages = timings.filter((t) => t.name === "postclose_stage_duration");
    body = (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "var(--space-4)" }}>
        <div style={block}>
          <div style={head}>数据源请求 · 成功率</div>
          {providers.length === 0 ? (
            <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>本进程启动以来暂无请求</div>
          ) : providers.map((p) => {
            const rows = req.filter((c) => c.labels.provider === p);
            const ok = rows.filter((c) => c.labels.outcome === "success").reduce((a, c) => a + c.value, 0);
            const total = rows.reduce((a, c) => a + c.value, 0);
            const fails = rows.filter((c) => c.labels.outcome !== "success").map((c) => `${c.labels.outcome} ${c.value}`).join(" / ");
            const rate = total ? (ok / total) * 100 : 0;
            return (
              <HealthRow key={p} label={p} value={`${rate.toFixed(1)}% (${ok}/${total})${fails ? ` · 失败 ${fails}` : ""}`}
                color={rate >= 95 ? "var(--market-down)" : rate >= 80 ? "var(--warning)" : "var(--market-up)"} />
            );
          })}
        </div>
        <div style={block}>
          <div style={head}>备用源切换</div>
          {fallbacks.length === 0 ? (
            <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>暂无切换</div>
          ) : fallbacks.map(([k, v]) => <HealthRow key={k} label={k} value={`${v} 次`} />)}
        </div>
        <div style={block}>
          <div style={head}>限速等待</div>
          <TimingTable rows={waits} labelOf={(r) => r.labels.provider ?? "—"} />
        </div>
        <div style={block}>
          <div style={head}>LLM 耗时</div>
          <TimingTable rows={llm} labelOf={(r) => [r.labels.client, r.labels.model].filter(Boolean).join(" · ") || "—"} />
        </div>
        <div style={{ ...block, gridColumn: "1 / -1" }}>
          <div style={head}>盘后各阶段耗时</div>
          <TimingTable rows={stages} labelOf={(r) => r.labels.stage ?? "—"} />
        </div>
      </div>
    );
  }
  return (
    <MetricCard
      kicker="运行时"
      title="数据源 · 限速 · LLM · 盘后阶段"
      actions={<span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>进程内统计，后端重启后清零</span>}
    >
      {body}
    </MetricCard>
  );
}

/** D 项净成本 + 板块(20cm/10cm)分解面板：北极星 D 项「净盈亏比≥2.0」目标一屏可见，
 * 并定位拖后腿的板块（如 10cm 净盈亏比 < 20cm）。与 self_check 板块拆分同源口径。 */
function DNetPanel({
  windowDays,
  sampleScope,
}: {
  windowDays: number | null;
  sampleScope: MetricsSampleScope;
}) {
  const { data, loading, error } = useApi<DNetBreakdown>(
    () => api.dNetBreakdown(windowDays ?? undefined, sampleScope),
    [windowDays, sampleScope],
  );

  return (
    <MetricCard
      kicker={sampleScope === "historical_replay" ? "历史回放研究" : "北极星 D 项"}
      title="净成本 + 板块(20cm/10cm)分解"
      actions={<span style={{ fontSize: 12, color: "var(--text-muted)" }}>跟随页面时间窗口</span>}
    >
      {loading ? (
        <CardSkeleton count={3} />
      ) : error ? (
        <HealthErrorNote msg="D 项净成本分解获取失败" />
      ) : !data || !data.net ? (
        <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
          窗口内无已结算{sampleScope === "historical_replay" ? "历史回放研究" : " LIVE formal"}推荐
        </div>
      ) : (
        <div style={{ display: "grid", gap: "var(--space-4)" }}>
          {/* 净成本概要 */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            <DNetKpi label="净胜率" value={data.net.net_win_rate != null ? `${data.net.net_win_rate.toFixed(1)}%` : "—"} tone={(data.net.net_win_rate ?? 0) >= 60 ? "up" : (data.net.net_win_rate ?? 0) >= 50 ? "neutral" : "down"} sub="扣 0.07%/笔" />
            <DNetKpi label="净盈亏比" value={data.net.net_profit_loss_ratio != null ? data.net.net_profit_loss_ratio.toFixed(2) : "—"} tone={(data.net.net_profit_loss_ratio ?? 0) >= 2.0 ? "up" : (data.net.net_profit_loss_ratio ?? 0) >= 1.4 ? "neutral" : "down"} sub={sampleScope === "historical_replay" ? "研究样本" : "目标 ≥2.0"} />
            <DNetKpi label="净均收益" value={data.net.net_avg_return != null ? `${data.net.net_avg_return >= 0 ? "+" : ""}${data.net.net_avg_return.toFixed(2)}%` : "—"} tone={(data.net.net_avg_return ?? 0) >= 0 ? "up" : "down"} sub="扣费后" />
            <DNetKpi label="最大亏损" value={data.net.max_loss != null ? `${data.net.max_loss.toFixed(2)}%` : "—"} tone="down" sub="单笔最差" />
            <DNetKpi label="样本" value={`${data.net.sample_count}`} tone="neutral" sub={sampleScope === "historical_replay" ? "历史回放研究" : "已结算 LIVE formal"} />
          </div>
          {/* 板块分解 */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "var(--space-3)" }}>
            <DNetBoardCard label="20cm · 创业板/科创板" board={data.board["20cm"]} healthy={data.board["20cm"]?.net_profit_loss_ratio ?? null} />
            <DNetBoardCard label="10cm · 主板/中小板" board={data.board["10cm"]} healthy={data.board["10cm"]?.net_profit_loss_ratio ?? null} />
          </div>
        </div>
      )}
    </MetricCard>
  );
}

/** 单个净成本 KPI（数字右对齐 + 等宽，涨红跌绿低饱和）。 */
function DNetKpi({ label, value, tone, sub }: { label: string; value: string; tone: "up" | "down" | "neutral"; sub?: string }) {
  const color = tone === "up" ? "var(--market-up)" : tone === "down" ? "var(--market-down)" : "var(--ink)";
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
      }}
    >
      <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginBottom: "var(--space-1)" }}>{label}</div>
      <div style={{ fontFamily: "var(--font-mono, monospace)", fontSize: "var(--text-lg)", fontWeight: 600, color }}>{value}</div>
      {sub && <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

/** 单板块分解卡：净盈亏比 < 2.0 标红（拖后腿），≥2.0 标绿（健康）。 */
function DNetBoardCard({ label, board, healthy }: { label: string; board: DNetBreakdown["board"]["20cm"]; healthy: number | null }) {
  if (!board) {
    return (
      <div style={{ background: "var(--surface-1)", border: "1px solid var(--line)", borderRadius: "var(--radius-sm, 6px)", padding: "var(--space-3)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
        {label}：窗口内无样本
      </div>
    );
  }
  const plColor = healthy != null ? (healthy >= 2.0 ? "var(--market-up)" : healthy >= 1.4 ? "var(--warning)" : "var(--market-down)") : "var(--ink)";
  return (
    <div style={{ background: "var(--surface-1)", border: "1px solid var(--line)", borderRadius: "var(--radius-sm, 6px)", padding: "var(--space-3)" }}>
      <div style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--ink)", marginBottom: "var(--space-2)" }}>{label}</div>
      <div style={{ display: "grid", gap: "var(--space-1)" }}>
        <HealthRow label="净盈亏比" value={board.net_profit_loss_ratio != null ? board.net_profit_loss_ratio.toFixed(2) : "—"} color={plColor} />
        <HealthRow label="净胜率" value={`${board.net_win_rate.toFixed(1)}%`} />
        <HealthRow label="净均收益" value={`${board.net_avg_return >= 0 ? "+" : ""}${board.net_avg_return.toFixed(2)}%`} />
        <HealthRow label="样本" value={`${board.n}`} />
      </div>
    </div>
  );
}

const MODE_LABEL: Record<string, string> = {
  no_follow_through: "无跟风",
  extension_chase: "追高延伸",
  weak_stock: "弱势股",
  gap_roundtrip: "跳空往返",
  other: "其他",
};

/** D 项真残留毒区 + 伪活跃 + triage 块（T32/T35）。 */
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
  const tr = data.triage;
  const topMode = data.modes?.[0];
  const ok = n === 0;
  const mostlyExplained = tr != null && tr.sparse === 0 && tr.n > 0;
  const meta = ok
    ? { label: "干净", color: "var(--market-down)" }
    : mostlyExplained
      ? { label: "已解释", color: "var(--warning)" }
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
        {tr && tr.n > 0 ? (
          <HealthRow
            label="triage"
            value={`污${tr.pure_pollution}/伪${tr.pseudo_active}/软${tr.soft_allowed ?? 0}/簇${tr.day_clustered}/稀${tr.sparse}`}
          />
        ) : topMode ? (
          <HealthRow
            label="主模式"
            value={`${MODE_LABEL[topMode.mode] ?? topMode.mode} ${topMode.n}·均${topMode.avg_return}%`}
          />
        ) : null}
        {pa && pa.weak_active_n > 0 ? (
          <HealthRow
            label="弱活伪活跃"
            value={`${pa.pseudo_n}/${pa.weak_active_n}（仍活跃 ${pa.still_active_n}）`}
          />
        ) : (
          <HealthRow label="弱活伪活跃" value="—" />
        )}
        {tr?.soft_allowed_items && tr.soft_allowed_items.length > 0 && (
          <div
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--text-muted)",
              lineHeight: 1.45,
              fontFamily: "var(--font-mono)",
            }}
            title={tr.soft_allowed_items.map((it) => `${it.trade_date} ${it.code} ${it.mode} — ${it.note}`).join("\n")}
          >
            允许soft:{" "}
            {tr.soft_allowed_items
              .slice(0, 3)
              .map((it) => `${it.code ?? "?"}`)
              .join(" · ")}
          </div>
        )}
        {tr?.sparse_items && tr.sparse_items.length > 0 && (
          <div
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--text-muted)",
              lineHeight: 1.45,
              fontFamily: "var(--font-mono)",
            }}
            title={tr.sparse_items.map((it) => `${it.trade_date} ${it.code} ${it.mode} — ${it.note}`).join("\n")}
          >
            稀疏:{" "}
            {tr.sparse_items
              .slice(0, 3)
              .map((it) => `${it.code ?? "?"}(${it.mode ?? "?"})`)
              .join(" · ")}
            {tr.sparse_same_day ? ` · 同日${tr.sparse_same_day}禁合并` : ""}
          </div>
        )}
        {(tr?.note || pa?.note) && (
          <div
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--text-muted)",
              lineHeight: 1.45,
              marginTop: 2,
            }}
            title={tr?.note || pa?.note || undefined}
          >
            {(() => {
              const note = tr?.note || pa?.note || "";
              return note.length > 72 ? `${note.slice(0, 72)}…` : note;
            })()}
          </div>
        )}
      </div>
    </div>
  );
}

/** 资金流因子预测力块（T1 新因子，待前瞻验证候选）—— /api/metrics/capital-flow-factor */
function CapitalFlowFactorBlock({
  data,
  loading,
  error,
}: {
  data: CapitalFlowFactorResponse | null | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <CardSkeleton count={3} />;
  if (error) return <HealthErrorNote msg="资金流因子获取失败" />;
  if (!data || data.factors.length === 0) {
    return (
      <div style={{ background: "var(--surface-1)", border: "1px solid var(--line)", borderRadius: "var(--radius-sm, 6px)", padding: "var(--space-3)" }}>
        <div style={{ fontWeight: 600, marginBottom: "var(--space-2)" }}>资金流因子预测力</div>
        <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>{data?.note ?? "样本不足"}</div>
      </div>
    );
  }
  const best = data.best_factor;
  const thS: CSSProperties = { padding: "var(--space-1) var(--space-2)", textAlign: "right", fontWeight: 500, fontSize: "var(--text-xs)" };
  const tdS: CSSProperties = { padding: "var(--space-1) var(--space-2)", fontSize: "var(--text-sm)" };
  return (
    <div style={{ background: "var(--surface-1)", border: "1px solid var(--line)", borderRadius: "var(--radius-sm, 6px)", padding: "var(--space-3)" }}>
      <div style={{ fontWeight: 600, marginBottom: "var(--space-2)" }}>资金流因子预测力（T1 候选 · n={data.sample_count}）</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--line)" }}>
              <th style={{ ...thS, textAlign: "left" }}>因子</th>
              <th style={thS}>n</th>
              <th style={thS}>r_pb</th>
              <th style={thS}>流入&gt;0 WR</th>
              <th style={thS}>流入≤0 WR</th>
              <th style={thS}>Δ</th>
            </tr>
          </thead>
          <tbody>
            {data.factors.map((f) => (
              <tr key={f.field} style={{ borderBottom: "1px solid var(--line)" }}>
                <td style={{ ...tdS, fontVariantNumeric: "tabular-nums" }}>
                  {f.name}
                  {best && f.field === best.field && (
                    <span style={{ color: "var(--accent-strong)", marginLeft: 4 }} title="最强预测力">★</span>
                  )}
                </td>
                <td style={{ ...tdS, fontVariantNumeric: "tabular-nums" }} className="text-muted">{f.n}</td>
                <td style={{ ...tdS, fontVariantNumeric: "tabular-nums", color: f.r_pb && f.r_pb > 0.1 ? "var(--market-down)" : "var(--text)" }}>
                  {f.r_pb == null ? "—" : f.r_pb.toFixed(4)}
                </td>
                <td style={{ ...tdS, fontVariantNumeric: "tabular-nums" }}>{f.wr_pos == null ? "—" : `${f.wr_pos}%`}</td>
                <td style={{ ...tdS, fontVariantNumeric: "tabular-nums" }}>{f.wr_neg == null ? "—" : `${f.wr_neg}%`}</td>
                <td style={{ ...tdS, fontVariantNumeric: "tabular-nums", color: (f.delta_pp ?? 0) > 5 ? "var(--market-down)" : "var(--text-muted)" }}>
                  {f.delta_pp == null ? "—" : `+${f.delta_pp}pp`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {best && best.guard && (
        <div style={{ marginTop: "var(--space-2)", fontSize: "var(--text-sm)" }}>
          <span style={{ color: best.guard.trustworthy ? "var(--market-down)" : "var(--text-muted)" }}>
            {best.guard.trustworthy ? "✅" : "❌"} feature_signal_guard
          </span>
          <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
            {best.guard.trustworthy
              ? `跨${best.guard.independent_days}日 top=${best.guard.top_day}(${(best.guard.top_day_ratio * 100).toFixed(0)}%)`
              : best.guard.reason}
          </span>
          <div className="text-muted" style={{ marginTop: 4, fontSize: "var(--text-xs)" }}>
            待前瞻验证候选（D 路径穷尽时不直接加 strategy.yaml）
          </div>
        </div>
      )}
    </div>
  );
}

/** phase/sentiment 信息量块（T34）：与 self_check D 项 phase 摘要同源。 */
function PhaseInformationBlock({
  data,
  loading,
  error,
}: {
  data: DDiagnostics | null | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <CardSkeleton count={3} />;
  if (error) return <HealthErrorNote msg="phase 信息量获取失败" />;
  if (!data) return null;
  const pi = data.phase_information;
  if (!pi) {
    return (
      <div
        style={{
          background: "var(--surface-1)",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius-sm, 6px)",
          padding: "var(--space-3)",
        }}
      >
        <div style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text-muted)" }}>
          phase 信息量 · —
        </div>
      </div>
    );
  }
  const ok = pi.regime_significant;
  const color = ok ? "var(--market-down)" : "var(--warning)";
  const label = ok ? "regime 有效" : "regime 无信息";
  const topLow = pi.notable_low_buckets?.[0];
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
            background: color,
            boxShadow: `0 0 0 3px color-mix(in srgb, ${color} 18%, transparent)`,
          }}
        />
        <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color }}>
          phase 信息量 · {label}
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        <HealthRow
          label="regime"
          value={`p=${Number(pi.regime_p).toFixed(3)}${pi.regime_significant ? "·显著" : "·否"}`}
        />
        <HealthRow
          label="sentiment"
          value={`p=${Number(pi.sentiment_p).toFixed(3)}${pi.sentiment_significant ? "·显著" : "·否"}`}
        />
        {topLow ? (
          <HealthRow
            label="亏桶"
            value={`${topLow.name} WR=${topLow.winrate}% n=${topLow.n}`}
          />
        ) : (
          <HealthRow label="亏桶" value="—" />
        )}
        {pi.note && (
          <div
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--text-muted)",
              lineHeight: 1.45,
              marginTop: 2,
            }}
            title={pi.note}
          >
            {pi.note.length > 80 ? `${pi.note.slice(0, 80)}…` : pi.note}
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
  if (isAiReviewDisabled(data)) {
    return (
      <div style={{ background: "var(--surface-1)", border: "1px solid var(--line)", borderRadius: "var(--radius-sm, 6px)", padding: "var(--space-3)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-2)" }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--muted)" }} />
          <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--muted)" }}>AI 审查已停用</span>
        </div>
        <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)", lineHeight: 1.6 }}>后端已停用 AI 风险审查（10-19 起），出票只看规则分；历史缓存 {data?.total_entries ?? 0} 条保留。</div>
      </div>
    );
  }
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
      {data.call_health && data.call_health.runs.length > 0 && (
        <CallHealthBlock runs={data.call_health.runs} anyUnhealthy={data.call_health.any_unhealthy} />
      )}
    </div>
  );
}

/** LLM 调用成功率块：最近5个 formal run 的 skill_review 成功率 + 失败原因。
 * 与缓存新鲜度正交：缓存命中旧 cache 时新鲜度 ok 但新 run 可能全失败（熔断/余额）。 */
function CallHealthBlock({
  runs,
  anyUnhealthy,
}: {
  runs: LlmReviewHealth["call_health"]["runs"];
  anyUnhealthy: boolean;
}) {
  const latest = runs[0]; // 最近的 run（runs 按 started_at DESC）
  const color = anyUnhealthy ? "var(--market-up)" : "var(--market-down)";
  return (
    <div
      style={{
        marginTop: "var(--space-3)",
        paddingTop: "var(--space-3)",
        borderTop: "1px solid var(--line)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-2)" }}>
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: color,
          }}
        />
        <span style={{ fontSize: "var(--text-xs)", fontWeight: 600, color }}>
          调用成功率 · {anyUnhealthy ? "异常" : "正常"}
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-1)" }}>
        {runs.slice(0, 3).map((r) => (
          <div
            key={r.trade_date}
            style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}
          >
            <span style={{ fontVariantNumeric: "tabular-nums" }}>{r.trade_date}</span>
            <span style={{ fontVariantNumeric: "tabular-nums", color: r.unhealthy ? "var(--market-up)" : "var(--text)" }}>
              {r.success}/{r.success + r.failed} ({r.rate.toFixed(0)}%)
            </span>
          </div>
        ))}
      </div>
      {latest && latest.top_reasons.length > 0 && (
        <div style={{ marginTop: "var(--space-1)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
          失败：{latest.top_reasons.map((x) => `${x.reason}=${x.count}`).join(" ")}
        </div>
      )}
      {latest && latest.sample_errors && latest.sample_errors.length > 0 && (
        <div style={{ marginTop: "var(--space-1)", fontSize: "var(--text-xs)", color: "var(--text-muted)", lineHeight: 1.5 }}>
          {latest.sample_errors.map((e, i) => (
            <div key={i} style={{ opacity: 0.8 }}>• {e}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 模型路由可用性块：每路由 base_url 可达性 + circuit_breaker 熔断状态。
 * 区分「配置错（base_url 不可达）」vs「上游故障熔断（可达但 503 累积触发）」。 */
function RouteHealthBlock({
  data,
  loading,
  error,
}: {
  data: LlmRouteHealthItem[] | null | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <CardSkeleton count={3} />;
  if (error) return <HealthErrorNote msg="路由可用性获取失败" />;
  if (!data || data.length === 0) return null;

  return (
    <div
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
      }}
    >
      <div style={{ marginBottom: "var(--space-3)", fontSize: "var(--text-sm)", fontWeight: 600 }}>
        模型路由可用性
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {data.map((r) => {
          const circuit = r.circuit;
          const blocked = circuit?.blocked ?? false;
          const color = !r.reachable
            ? "var(--market-up)"
            : blocked
              ? "var(--warning)"
              : "var(--market-down)";
          return (
            <div key={r.route_id} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }} />
                <span style={{ fontSize: "var(--text-xs)", fontWeight: 600, color }}>
                  {r.name} · {r.model}
                </span>
              </div>
              <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", paddingLeft: "var(--space-3)" }}>
                {r.reachable
                  ? `可达 (${r.status_code}, ${r.model_count} 模型)`
                  : `不可达: ${r.error ?? "未知"}`}
                {blocked && ` · 熔断中（连续失败 ${circuit?.consecutive_failures}）`}
              </div>
            </div>
          );
        })}
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
  sampleScope,
  onScopeSelect,
  dateRange,
  historicalCohort,
}: {
  windowDays: number | null;
  onSelect: (v: number | null) => void;
  sampleScope: MetricsSampleScope;
  onScopeSelect: (v: MetricsSampleScope) => void;
  dateRange?: DateRangeMeta;
  historicalCohort?: HistoricalReconstructionCohort | null;
}) {
  const isReplay = sampleScope === "historical_replay";
  return (
    <div className="panel" style={{ padding: "var(--space-3) var(--space-4)", display: "grid", gap: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "var(--text-muted)", fontWeight: 600 }}>样本口径</span>
        <div style={{ display: "flex", gap: "var(--space-1)" }}>
          {SAMPLE_SCOPE_OPTIONS.map((opt) => {
            const active = sampleScope === opt.value;
            return (
              <button
                key={opt.value}
                onClick={() => onScopeSelect(opt.value)}
                style={scopeButtonStyle(active)}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
        <span
          className={`tag ${isReplay ? "tag--warning" : ""}`}
          title={isReplay
            ? "由真实历史生成时间、as-of 快照、策略/执行 policy 与代码版本共同约束，不进入 LIVE KPI"
            : "只统计 provenance 完整、因果边界成立的 LIVE_FORWARD 推荐"}
        >
          {isReplay ? "研究证据 · 不计入 LIVE KPI" : "前瞻正式证据"}
        </span>
      </div>
      {isReplay && historicalCohort && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-3)",
            flexWrap: "wrap",
            fontSize: 12,
            color: "var(--text-muted)",
          }}
          title={
            (historicalCohort.batches?.length ?? 0) > 1
              ? [
                  `合并 ${historicalCohort.batches!.length} 个策略批次（每个策略取最新有效批次）`,
                  `cohort seal ${historicalCohort.cohort_identity_hash}`,
                  ...historicalCohort.batches!.map(
                    (b) =>
                      `· ${b.strategy_label || b.strategy_key || "?"}  ${b.window_start}~${b.window_end}  ${b.completed_trade_date_count} 天  batch ${b.batch_id.slice(0, 17)}  code ${b.code_revision.slice(0, 8)}  完成于 ${b.completed_at.slice(0, 16)}`,
                  ),
                ].join("\n")
              : `batch ${historicalCohort.batch_id}${historicalCohort.strategy_label ? `\nstrategy ${historicalCohort.strategy_label}` : ""}\ncohort seal ${historicalCohort.cohort_identity_hash}\nstrategy policy ${historicalCohort.strategy_policy_fingerprint}\nexecution policy ${historicalCohort.execution_policy_fingerprint}\ncode ${historicalCohort.code_revision}`
          }
        >
          <span>研究 cohort</span>
          {(historicalCohort.batch_count ?? 1) > 1 ? (
            <span className="tag">{historicalCohort.batch_count} 个策略批次合并</span>
          ) : historicalCohort.strategy_label ? (
            <span>{historicalCohort.strategy_label}</span>
          ) : null}
          <span style={{ fontFamily: "var(--font-mono)" }}>
            {historicalCohort.window_start} ~ {historicalCohort.window_end}
          </span>
          <span>
            {historicalCohort.completed_trade_date_count}/{historicalCohort.expected_trade_date_count} 个交易日
          </span>
          <span style={{ fontFamily: "var(--font-mono)" }}>
            seal {historicalCohort.cohort_identity_hash.slice(0, 12)}
          </span>
          <span style={{ fontFamily: "var(--font-mono)" }}>
            code {historicalCohort.code_revision.split(",").map((rev) => rev.slice(0, 8)).join(",")}
          </span>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "var(--text-muted)", fontWeight: 600 }}>时间窗口</span>
        <div style={{ display: "flex", gap: "var(--space-1)" }}>
          {WINDOW_OPTIONS.map((opt) => {
            const active = windowDays === opt.value;
            return (
              <button
                key={String(opt.value)}
                onClick={() => onSelect(opt.value)}
                style={scopeButtonStyle(active)}
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

function scopeButtonStyle(active: boolean): CSSProperties {
  return {
    padding: "4px 12px",
    fontSize: 13,
    borderRadius: 4,
    border: "1px solid var(--border)",
    background: active ? "var(--color-accent)" : "var(--surface-1)",
    color: active ? "#fff" : "var(--text-secondary)",
    cursor: "pointer",
    fontWeight: active ? 600 : 400,
  };
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
      sub: `${data.success_count}/${data.executed_trade_count} 笔已成交`,
    },
    {
      label: "盈亏比",
      value: data.profit_loss_ratio != null ? data.profit_loss_ratio.toFixed(2) : "—",
      tone: data.profit_loss_ratio >= 1.4 ? "up" : "neutral",
      sub: "win/loss",
    },
    {
      // 只算已成交并卖出的每笔净收益；没成交的推荐只计入成交率，不当 0% 摊进均值（旧 average_net_return 会把亏损冲淡）。
      label: "每笔净收益（已成交）",
      value: data.executed_trade_count ? `${data.avg_executed_return.toFixed(2)}%` : "—",
      tone: (data.avg_executed_return ?? 0) >= 0 ? "up" : "down",
      sub: `${data.executed_trade_count} 笔已成交 · 已扣成本${data.fill_split ? " · 含按开盘价算的，分开看见下表" : ""}`,
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
      label: "成交率",
      value: data.trigger_rate != null ? `${(data.trigger_rate * 100).toFixed(1)}%` : "—",
      tone: "neutral",
      sub: `${data.executed_trade_count}/${data.labeled_count} 已结算推荐`,
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
      title={data.era_label ? `核心 KPI · ${data.era_label}` : "核心 KPI"}
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
      {data.fill_split && (
        <LiveFillSplitTable split={data.fill_split} combo={data.combo_reference ?? null} other={data.other_reference ?? null} />
      )}
    </MetricCard>
  );
}

/** 实盘成交分两栏：盘中真的确认买入 vs 只是按开盘价模拟成交；旧组合方案单列一行，只作历史参考。 */
type LabeledSplit = LiveFillSplit & { label: string };

function LiveFillSplitTable({ split, combo, other }: { split: LiveFillSplit; combo: LabeledSplit | null; other: LabeledSplit | null }) {
  const pctText = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
  const tone = (v: number | null | undefined) =>
    v == null ? "var(--text-muted)" : v >= 0 ? "var(--market-up)" : "var(--market-down)";
  const cell = (s: LiveFillStats) => (
    <span style={{ fontFamily: "var(--font-mono, monospace)" }}>
      {s.n} 笔 · <span style={{ color: tone(s.avg_net_return) }}>{pctText(s.avg_net_return)}</span>
    </span>
  );
  const th = { textAlign: "left" as const, padding: "var(--space-2)", fontWeight: 600, color: "var(--text-muted)" };
  const td = { padding: "var(--space-2)" };
  return (
    <div style={{ marginTop: "var(--space-3)", overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-sm)" }}>
        <caption style={{ textAlign: "left", fontSize: "var(--text-xs)", color: "var(--text-muted)", paddingBottom: "var(--space-1)" }}>
          已卖出的票，每笔扣成本后的收益。「真买到」= 盘中确认过买入；「按开盘价算」= 盘中没确认，只是假设开盘买到。
        </caption>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--line)" }}>
            <th scope="col" style={th}>方案</th>
            <th scope="col" style={th}>真买到（盘中确认）</th>
            <th scope="col" style={th}>按开盘价算（没确认）</th>
            <th scope="col" style={th}>没买成</th>
          </tr>
        </thead>
        <tbody>
          <tr style={{ borderBottom: "1px solid var(--line)" }}>
            <th scope="row" style={{ ...td, textAlign: "left", fontWeight: 600 }}>单策略（现在用的）</th>
            <td style={td}>{cell(split.confirmed)}</td>
            <td style={td}>{cell(split.open_simulated)}</td>
            <td style={td}>{split.not_filled_count + split.confirmed_not_filled_count} 只</td>
          </tr>
          {other && (
            <tr style={{ borderBottom: "1px solid var(--line)" }}>
              <th scope="row" style={{ ...td, textAlign: "left", fontWeight: 400 }}>{other.label}</th>
              <td style={td}>{cell(other.confirmed)}</td>
              <td style={td}>{cell(other.open_simulated)}</td>
              <td style={td}>{other.not_filled_count + other.confirmed_not_filled_count} 只</td>
            </tr>
          )}
          {combo && (
            <tr style={{ color: "var(--text-muted)" }}>
              <th scope="row" style={{ ...td, textAlign: "left", fontWeight: 400 }}>{combo.label}</th>
              <td style={td}>{cell(combo.confirmed)}</td>
              <td style={td}>{cell(combo.open_simulated)}</td>
              <td style={td}>{combo.not_filled_count + combo.confirmed_not_filled_count} 只</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
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
            background: "var(--surface-strong)",
            border: "1px solid var(--line)",
            borderRadius: 8,
            fontSize: 13,
            lineHeight: 1.7,
            color: "var(--ink-secondary)",
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
          <span style={{ color: "var(--muted)", display: "block", marginTop: 6 }}>
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
  // by-strategy 不带 enabled 字段：用策略清单 /api/strategies 判定，默认隐藏已停用/已下线策略
  const { data: list } = useApi<StrategyListItem[]>(() => api.strategies(), []);
  const [showDisabled, setShowDisabled] = useState(false);
  if (rows.length === 0) return null;
  const enabledIds = list ? new Set(list.filter((s) => s.enabled).map((s) => s.skill_id)) : null;
  const visible = showDisabled || !enabledIds ? rows : rows.filter((r) => enabledIds.has(r.strategy_id));
  const hidden = rows.length - visible.length;
  const sorted = [...visible].sort((a, b) => b.hit_rate - a.hit_rate);
  return (
    <MetricCard
      kicker="分析"
      title="策略胜率"
      actions={
        <span style={{ color: "var(--text-muted)", fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
          按胜率降序
          {enabledIds && (hidden > 0 || showDisabled) && (
            <button className="btn btn--ghost" style={{ fontSize: 12 }} onClick={() => setShowDisabled((v) => !v)}>
              {showDisabled ? "隐藏已停用" : `显示已停用（${hidden}）`}
            </button>
          )}
        </span>
      }
    >
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {sorted.length === 0 && (
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>启用中的策略暂无样本</div>
        )}
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
