import { useState, type CSSProperties, type ReactNode } from "react";
import { ChevronDown, ChevronRight, FileText, History, Layers, Target } from "lucide-react";
import { api } from "../api/client";
import type {
  StandaloneMetricsResponse,
  StandaloneWindowMetrics,
  PresetStatsRow,
  StrategyBacktestReturns,
  StrategyListItem,
  StrategyPerformance,
} from "../types";
import { ErrorPage, EmptyState, PageSkeleton } from "../components/StateViews";
import { Panel } from "../components/Panel";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";

type StrategyWindowDays = 30 | 180;

/**
 * 策略清单页 —— 系统全部策略的"驾驶舱视图"。
 *
 * 只展示每日独立运行的现役策略（后端 /api/strategies 默认按 standalone 名单过滤，退役策略自动隐藏）。
 * 直接填补北极星"策略清单页"缺口（8 策略 + 简介 + 正式实盘胜率），
 * 并把"覆盖 3/8 → 8/8"的差距显式呈现：已调用的按胜率排序在前，从未调用的沉底并标注"未覆盖"。
 *
 * 设计：克制优先。bold 只落在胜率一个数字上；其余保持安静。A 股语义：涨/胜=红，跌/亏=绿，低饱和。
 */

export function StrategiesPage() {
  const [tab, setTab] = useState<"skills" | "presets">("skills");
  const [windowDays, setWindowDays] = useState<StrategyWindowDays>(180);
  const skills = useApi<StrategyListItem[]>(
    () => api.strategies(),
  );
  // 预设表现：后端 /api/stats/by-preset 已有完整数据，此前无任何页面消费（T7-C 缺口）。
  const presets = useApi<PresetStatsRow[]>(() => api.statsByPreset());

  return (
    <Panel
      kicker="策略"
      title="策略清单"
      actions={
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <TabButton active={tab === "skills"} onClick={() => setTab("skills")}>策略</TabButton>
          <TabButton active={tab === "presets"} onClick={() => setTab("presets")}>预设表现</TabButton>
        </div>
      }
    >
      <StandaloneMetricsSection />
      {tab === "skills" ? (
        <SkillsView skills={skills} windowDays={windowDays} onWindowDaysChange={setWindowDays} />
      ) : (
        <PresetsView presets={presets} />
      )}
    </Panel>
  );
}

/** 克制 tab 切换：复用既有窗口按钮视觉语言，不引入新样式/弹窗。 */
function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "4px 12px",
        fontSize: 12,
        borderRadius: 4,
        border: "1px solid var(--border)",
        background: active ? "var(--accent, #2563eb)" : "var(--surface-soft)",
        color: active ? "#fff" : "var(--muted)",
        cursor: "pointer",
        fontWeight: active ? 600 : 400,
      }}
    >
      {children}
    </button>
  );
}

/** 原策略清单视图（已调用/未调用卡片网格 + 时间窗口） */
function SkillsView({
  skills,
  windowDays,
  onWindowDaysChange,
}: {
  skills: { data: StrategyListItem[] | null; loading: boolean; error: string };
  windowDays: StrategyWindowDays;
  onWindowDaysChange: (value: StrategyWindowDays) => void;
}) {
  if (skills.loading) return <PageSkeleton title="策略清单" hint="加载策略与胜率" rowCount={8} />;
  if (skills.error) return <ErrorPage title="策略加载失败" message={skills.error} />;
  // 后端已按现役名单过滤；前端再兜底去掉停用项，避免旧缓存/旧后端把退役卡片带回来。
  const data = (skills.data ?? []).filter((s) => s.enabled);
  if (data.length === 0) {
    return <EmptyState icon={<Layers size={32} />} title="暂无策略" reason="系统尚未注册任何策略" />;
  }

  const ready = data.filter((s) => s.performance);
  const pending = data.filter((s) => !s.performance);

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-3)", padding: "var(--space-2) 0 var(--space-4)" }}>
        <div style={{ color: "var(--muted)", fontSize: 12 }}>
          三窗回测收益（单策略组合，见卡内口径）· 当前版本 canonical 画像另计 · 不混入旧版本真实收益
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexShrink: 0 }}>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>窗口</span>
          <TabButton active={windowDays === 30} onClick={() => onWindowDaysChange(30)}>30天</TabButton>
          <TabButton active={windowDays === 180} onClick={() => onWindowDaysChange(180)}>180天</TabButton>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: "var(--space-4)" }}>
        {ready.map((s) => (
          <CalledCard key={s.skill_id} item={s} windowDays={windowDays} />
        ))}
        {pending.map((s) => (
          <DormantCard key={s.skill_id} item={s} />
        ))}
      </div>
    </>
  );
}

/**
 * 预设表现视图（T7-C 缺口填补）：消费 /api/stats/by-preset，
 * 直接服务北极星 E「策略曝光公平度」——从 preset 维度看各运行组合的样本数/胜率/均收益，
 * 与「策略」tab 的 skill 维度互补。表格化、等宽数字、涨红跌绿、样本<20 显式标注噪声。
 */
function PresetsView({ presets }: { presets: { data: PresetStatsRow[] | null; loading: boolean; error: string } }) {
  if (presets.loading) return <PageSkeleton title="预设表现" hint="加载预设胜率" rowCount={6} />;
  if (presets.error) return <ErrorPage title="预设加载失败" message={presets.error} />;
  const rows = presets.data;
  if (!rows || rows.length === 0) {
    return <EmptyState icon={<Layers size={32} />} title="暂无预设表现" reason="系统尚未登记任何运行预设" />;
  }
  return <PresetTable rows={rows} />;
}

/** 预设表现表格：列 = 预设 / 运行数 / 推荐样本 / 胜率 / 均收益 / 均分 / 最近运行。
 * 克制：bold 只落胜率与均收益两个 D 项核心数字；其余安静；涨红跌绿按 A 股语义。 */
function PresetTable({ rows }: { rows: PresetStatsRow[] }) {
  const sorted = [...rows].sort((a, b) => (b.hit_rate ?? -1) - (a.hit_rate ?? -1));
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums" }}>
        <thead>
          <tr style={{ color: "var(--muted)", fontSize: "var(--text-xs)", textAlign: "right" }}>
            <th style={{ textAlign: "left", padding: "var(--space-2) var(--space-3)", fontWeight: 500 }}>预设</th>
            <th style={{ padding: "var(--space-2) var(--space-3)", fontWeight: 500 }}>运行</th>
            <th style={{ padding: "var(--space-2) var(--space-3)", fontWeight: 500 }}>推荐样本</th>
            <th style={{ padding: "var(--space-2) var(--space-3)", fontWeight: 500 }}>胜率</th>
            <th style={{ padding: "var(--space-2) var(--space-3)", fontWeight: 500 }}>均收益</th>
            <th style={{ padding: "var(--space-2) var(--space-3)", fontWeight: 500 }}>均分</th>
            <th style={{ textAlign: "left", padding: "var(--space-2) var(--space-3)", fontWeight: 500 }}>最近运行</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const win = r.hit_rate;
            const winColor =
              win == null ? "var(--muted)" : win >= 0.6 ? "var(--market-up)" : win >= 0.5 ? "var(--market-up)" : "var(--market-down)";
            const ret = r.avg_return;
            const retColor = ret == null ? "var(--muted)" : ret >= 0 ? "var(--market-up)" : "var(--market-down)";
            // 统计可信度：样本<20 时胜率属噪声（与 skill 卡片同源阈值），显式标注防过度信任
            const lowSample = (r.recommendation_count ?? 0) < 20;
            return (
              <tr key={r.preset_id ?? r.preset_name} style={{ borderTop: "1px solid var(--border)", fontSize: "var(--text-sm)" }}>
                <td style={{ textAlign: "left", padding: "var(--space-3) var(--space-3)", color: "var(--text)" }}>{r.preset_name}</td>
                <td style={{ textAlign: "right", padding: "var(--space-3) var(--space-3)", color: "var(--muted)" }}>{r.run_count}</td>
                <td style={{ textAlign: "right", padding: "var(--space-3) var(--space-3)", color: lowSample ? "var(--warning, #b45309)" : "var(--muted)" }}>
                  {r.recommendation_count}
                  {lowSample ? " ⚠" : ""}
                </td>
                <td style={{ textAlign: "right", padding: "var(--space-3) var(--space-3)", fontWeight: 700, color: winColor }}>
                  {win == null ? "—" : `${(win * 100).toFixed(1)}%`}
                </td>
                <td style={{ textAlign: "right", padding: "var(--space-3) var(--space-3)", fontWeight: 600, color: retColor }}>
                  {ret == null ? "—" : `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%`}
                </td>
                <td style={{ textAlign: "right", padding: "var(--space-3) var(--space-3)", color: "var(--muted)" }}>
                  {r.avg_score == null ? "—" : r.avg_score.toFixed(1)}
                </td>
                <td style={{ textAlign: "left", padding: "var(--space-3) var(--space-3)", color: "var(--muted)" }}>
                  {r.last_run_at ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 当前版本策略画像：每个信号日按评分取 Top 10，不含仓位或资金模型。 */
function CalledCard({ item, windowDays }: { item: StrategyListItem; windowDays: StrategyWindowDays }) {
  const perf = item.performance as StrategyPerformance;
  const { navigate } = useUrlRoute();
  const [open, setOpen] = useState(false);
  const selectedWindow = perf.window_metrics?.[String(windowDays)];
  const win = selectedWindow?.hit_rate ?? (windowDays === 180 ? perf.hit_rate : null);
  const winColor = win == null ? "var(--muted)" : win >= 0.6 ? "var(--market-up)" : win >= 0.5 ? "var(--market-up)" : "var(--market-down)";
  const pf = selectedWindow?.profit_loss_ratio ?? (windowDays === 180 ? perf.profit_loss_ratio : null);
  const avgReturn = selectedWindow?.avg_return ?? (windowDays === 180 ? perf.avg_return : null);
  const retColor = avgReturn == null ? "var(--muted)" : avgReturn >= 0 ? "var(--market-up)" : "var(--market-down)";
  const pfColor = pf == null ? "var(--muted)" : pf >= 1.4 ? "var(--market-up)" : pf >= 1.0 ? "var(--text)" : "var(--market-down)";
  const displaySampleCount = selectedWindow?.sample_count ?? (windowDays === 180 ? perf.sample_count : 0);
  const selectedSignalCount = selectedWindow?.selected_signal_count ?? (windowDays === 180 ? perf.selected_signal_count : 0);
  const rawSignalCount = selectedWindow?.raw_signal_count ?? (windowDays === 180 ? perf.raw_signal_count : 0);
  // 统计可信度：所选窗口样本<20 时胜率属噪声，须显式提示，防过度信任
  const lowSample = displaySampleCount < 20;
  // 最新补跑门线明细（T27：与 self_check gate_blocked 同源）
  const gate = item.last_exp_gate ?? perf.last_exp_gate;

  return (
    <div className="strat-card" style={cardStyle}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--space-2)" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 650, fontSize: "var(--text-lg)", color: "var(--text)", lineHeight: 1.2 }}>{item.name}</div>
          <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginTop: 2, fontFamily: "var(--font-mono)" }}>{item.skill_id}</div>
        </div>
        <StatusPill enabled={item.enabled} />
      </div>

      <p style={{ margin: "var(--space-3) 0 0", color: "var(--muted)", fontSize: "var(--text-sm)", lineHeight: 1.5, minHeight: 38 }}>
        {item.description || "（无简介）"}
      </p>

      {/* 三项均为 Top10 入选信号的逐笔统计，不推导账户收益。 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "var(--space-3)", marginTop: "var(--space-4)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)" }}>
        <Metric label={`${windowDays}天胜率`} value={win == null ? "—" : `${(win * 100).toFixed(1)}%`} color={winColor} />
        <Metric label="盈亏比" value={pf == null ? "—" : pf.toFixed(2)} color={pfColor} />
        <Metric
          label="平均单笔收益"
          value={avgReturn == null ? "—" : `${avgReturn >= 0 ? "+" : ""}${avgReturn.toFixed(2)}%`}
          color={retColor}
        />
      </div>
      <div style={{ marginTop: "var(--space-2)", fontSize: "var(--text-xs)", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
        {selectedWindow?.date_from ?? perf.date_from} ~ {selectedWindow?.date_to ?? perf.date_to} · Top10 入选 {selectedSignalCount} 个 · 已结算 {displaySampleCount} 个 · 原始信号 {rawSignalCount} 个
      </div>
      <div style={{ marginTop: 4, fontSize: "var(--text-xs)", color: "var(--muted)" }}>
        口径：每策略每日评分 Top {perf.daily_selection_limit}；不含仓位、资金占用和账户复利
      </div>
      {perf.window_rolled && perf.target_date_to && perf.target_date_to !== perf.date_to && (
        <div style={{ marginTop: 4, fontSize: "var(--text-xs)", color: "var(--warning, #b45309)", fontFamily: "var(--font-mono)" }} title={`目标窗口 ${perf.target_date_from} ~ ${perf.target_date_to} 随交易日漂移，暂复用同版本最近完成画像`}>
          信号窗口 {perf.date_from} ~ {perf.date_to}（随交易日滚动复用）
        </div>
      )}
      <div style={{ marginTop: 4, fontSize: "var(--text-xs)", color: "var(--muted)", fontFamily: "var(--font-mono)" }} title={perf.strategy_version}>
        当前版本 {perf.strategy_version.slice(0, 18)} · 更新 {perf.generated_at?.slice(0, 16).replace("T", " ") || "—"}
      </div>

        {lowSample && (
          <div
            style={{
              marginTop: "var(--space-3)",
              display: "flex",
              flexDirection: "column",
              gap: 4,
              fontSize: "var(--text-xs)",
              color: "var(--warning, #b45309)",
              background: "color-mix(in srgb, var(--warning, #b45309) 12%, transparent)",
              border: "1px solid color-mix(in srgb, var(--warning, #b45309) 30%, transparent)",
              borderRadius: 8,
              padding: "6px 10px",
              alignSelf: "flex-start",
              maxWidth: "100%",
            }}
            title={
              gate?.gate_blocked
                ? `最新补跑 0 产出：有效线 ${gate.effective_line ?? "—"} vs 最高候选 ${gate.max_candidate_score ?? "无"}。` +
                  (gate.panic_day
                    ? `恐慌日跌停 ${gate.limit_down_count ?? "—"} 家抬线（设计内保护，准确>可见），勿放宽门线。`
                    : `regime 有效线偏高/无高分票，非补跑故障（准确>可见），勿放宽门线。`)
                : perf.exposure_30d != null && (perf.exposure_floor ?? 20) > (perf.exposure_30d ?? 0)
                  ? `近30天 formal+experiment 曝光 ${perf.exposure_30d} < 下限 ${perf.exposure_floor ?? 20}；` +
                    `盘前补跑会继续喂样本。0 产出=当前 regime/universe 拦截（准确>可见），勿放宽门槛。`
                  : `已结算样本 < 20，胜率属噪声，仅供参考。`
            }
          >
            <span>样本不足（n={displaySampleCount}）· 胜率统计仅供参考</span>
            {perf.exposure_30d != null && (
              <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                近30天曝光 {perf.exposure_30d}
                {(perf.exposure_floor ?? 20) > perf.exposure_30d
                  ? gate?.gate_blocked
                    ? gate.panic_day
                      ? ` / ${perf.exposure_floor ?? 20} · 门线拦截·恐慌日`
                      : ` / ${perf.exposure_floor ?? 20} · 门线拦截`
                    : ` / ${perf.exposure_floor ?? 20} · 补跑在喂`
                  : ` ≥${perf.exposure_floor ?? 20}`}
              </span>
            )}
            {gate?.gate_blocked && (
              <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                有效线{" "}
                {gate.effective_line != null ? Number(gate.effective_line).toFixed(1) : "—"}
                {" vs 最高候选 "}
                {gate.max_candidate_score != null
                  ? Number(gate.max_candidate_score).toFixed(1)
                  : "无"}
                {gate.panic_day
                  ? ` · 恐慌日ld=${gate.limit_down_count ?? "—"}`
                  : gate.regime
                    ? ` · ${gate.regime}`
                    : ""}
              </span>
            )}
          </div>
        )}

      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          marginTop: "var(--space-3)",
          background: "transparent",
          border: "none",
          color: "var(--accent)",
          cursor: "pointer",
          fontSize: "var(--text-sm)",
          padding: 0,
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {open ? "收起" : `${windowDays}天按月信号表现`}
      </button>

      {open && (
        <div style={{ marginTop: "var(--space-3)", display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "var(--space-2)" }}>
          {(selectedWindow?.monthly_metrics ?? perf.monthly_metrics).map((month) => {
            const c = month.avg_return == null ? "var(--muted)" : month.avg_return >= 0 ? "var(--market-up)" : "var(--market-down)";
            return (
              <div key={month.month} style={{ background: "var(--surface)", borderRadius: 8, padding: "var(--space-2)", textAlign: "center" }}>
                <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)" }}>{month.month}</div>
                <div style={{ fontSize: "var(--text-xs)", fontFamily: "var(--font-mono)", color: c }}>
                  胜率 {month.hit_rate == null ? "—" : `${(month.hit_rate * 100).toFixed(1)}%`}
                </div>
                <div style={{ fontSize: 10, color: c }}>
                  均收益 {month.avg_return == null ? "—" : `${month.avg_return >= 0 ? "+" : ""}${month.avg_return.toFixed(2)}%`}
                </div>
                <div style={{ fontSize: 10, color: "var(--muted)" }}>Top10 {month.selected_signal_count} / 原始 {month.raw_signal_count}</div>
              </div>
            );
          })}
        </div>
      )}

      <button
        onClick={() => navigate("/history", { params: { strategy_id: item.skill_id, strategy_name: item.name } })}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          marginTop: "var(--space-3)",
          background: "transparent",
          border: "none",
          color: "var(--accent)",
          cursor: "pointer",
          fontSize: "var(--text-sm)",
          padding: 0,
        }}
      >
        <History size={14} /> 查看该策略推荐历史 →
      </button>

      <BacktestReturnsBlock item={item} />

      <StrategyDetailLink item={item} />

      <CardFooter item={item} />
    </div>
  );
}

/** 单个核心指标：标签在上、等宽大数字右对齐，颜色由调用方按涨红跌绿给出 */
function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ textAlign: "right", minWidth: 0 }}>
      <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.2, color, fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
    </div>
  );
}

/** 当前版本画像未生成：明确 pending，不用旧版本真实收益补位。 */
function DormantCard({ item }: { item: StrategyListItem }) {
  return (
    <div className="strat-card" style={{ ...cardStyle, opacity: 0.82 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--space-2)" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 650, fontSize: "var(--text-lg)", color: "var(--text)", lineHeight: 1.2 }}>{item.name}</div>
          <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginTop: 2, fontFamily: "var(--font-mono)" }}>{item.skill_id}</div>
        </div>
        <span style={pillStyle("var(--muted)", "color-mix(in srgb, var(--muted) 14%, transparent)")}>待回测</span>
      </div>

      <p style={{ margin: "var(--space-3) 0 0", color: "var(--muted)", fontSize: "var(--text-sm)", lineHeight: 1.5, minHeight: 38 }}>
        {item.description || "（无简介）"}
      </p>

      <div style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: "var(--space-2)", color: "var(--muted)", fontSize: "var(--text-sm)" }}>
        <Target size={14} />
        当前版本半年画像尚未生成；不会展示跨版本混合收益
      </div>

      <BacktestReturnsBlock item={item} />

      <StrategyDetailLink item={item} />
      <CardFooter item={item} />
    </div>
  );
}

/**
 * 三窗回测收益块：把本机跑出的单策略回测收益率直接展示在策略卡上。
 *
 * 口径（与 canonical_backtest 画像不同，必须显式标注，禁止混算）：
 * 只保留该技能有非否决策略信号的候选卡 → 真实 selection v4（score_line 72 / Top10）
 * → execution v4 因果出场；逐笔等权，不含仓位与资金模型。属 path A 诊断证据。
 */
function BacktestReturnsBlock({ item }: { item: StrategyListItem }) {
  const bt = item.backtest_returns as StrategyBacktestReturns | null | undefined;
  if (!bt) return null;
  const windows = ["W1", "W2", "W3"];
  const cellLabel: CSSProperties = {
    fontSize: "var(--text-xs)",
    color: "var(--muted)",
    fontFamily: "var(--font-mono)",
  };
  return (
    <div
      style={{
        marginTop: "var(--space-3)",
        paddingTop: "var(--space-3)",
        borderTop: "1px dashed var(--border)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text)", fontWeight: 600 }}>
          {bt.caliber?.label ?? "三窗回测收益"}（单策略组合）
        </span>
        <span
          style={cellLabel}
          title={bt.caliber?.caveat ?? ""}
        >
          {bt.windows_with_trades}/3 窗有样本
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: "var(--space-2)",
          marginTop: "var(--space-2)",
        }}
      >
        {windows.map((w) => {
          const m = bt.windows?.[w];
          const meta = bt.windows_meta?.[w];
          if (!m) {
            return (
              <div key={w} style={{ background: "var(--surface)", borderRadius: 8, padding: "var(--space-2)" }}>
                <div style={cellLabel} title={meta?.label}>
                  {w}
                </div>
                <div style={{ fontSize: "var(--text-sm)", color: "var(--muted)" }}>无成交</div>
              </div>
            );
          }
          const color =
            m.avg_net_pct >= 0 ? "var(--market-up)" : "var(--market-down)";
          return (
            <div
              key={w}
              style={{ background: "var(--surface)", borderRadius: 8, padding: "var(--space-2)" }}
              title={`${meta?.label ?? w}（${meta?.from ?? ""} ~ ${meta?.to ?? ""}）\nn=${m.n} · 出票日 ${m.trade_days}\n胜率 ${m.win_rate_pct}% · 盈亏比 ${m.profit_factor}\n累计 ${m.sum_net_pct}% · 最大回撤 ${m.max_drawdown_pct}%\n闸门 ${m.challenger ?? "—"}`}
            >
              <div style={cellLabel}>{w}</div>
              <div style={{ fontSize: "var(--text-lg)", fontWeight: 700, color }}>
                {m.avg_net_pct >= 0 ? "+" : ""}
                {m.avg_net_pct.toFixed(2)}%
              </div>
              <div style={cellLabel}>
                WR {m.win_rate_pct.toFixed(1)}% · PF{" "}
                {m.profit_factor == null ? "—" : m.profit_factor.toFixed(2)}
              </div>
              <div style={cellLabel}>
                Σ {m.sum_net_pct >= 0 ? "+" : ""}
                {m.sum_net_pct.toFixed(1)}% · n {m.n}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ ...cellLabel, marginTop: 4 }} title={bt.caliber?.description ?? ""}>
        口径 {bt.caliber?.selection_policy} + {bt.caliber?.execution_policy} · 三窗平均单笔{" "}
        {bt.avg_net_pct_mean == null
          ? "—"
          : `${bt.avg_net_pct_mean >= 0 ? "+" : ""}${bt.avg_net_pct_mean.toFixed(2)}%`}
        {" · "}
        组合基线{" "}
        {["W1", "W2", "W3"]
          .map((w) => {
            const c = bt.combined?.[w];
            return c ? `${w} ${c.avg_net_pct >= 0 ? "+" : ""}${c.avg_net_pct.toFixed(2)}%` : `${w} —`;
          })
          .join(" / ")}
        {" · "}
        {bt.generated_at?.slice(0, 10) ?? "—"}
      </div>
    </div>
  );
}

function StrategyDetailLink({ item }: { item: StrategyListItem }) {
  const { navigate } = useUrlRoute();
  return (
    <button
      onClick={() => navigate(`/strategies/${encodeURIComponent(item.skill_id)}`)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        marginTop: "var(--space-3)",
        background: "transparent",
        border: "none",
        color: "var(--text)",
        cursor: "pointer",
        fontSize: "var(--text-sm)",
        padding: 0,
        fontWeight: 600,
      }}
    >
      <FileText size={14} /> 查看策略内容与版本 →
    </button>
  );
}

function CardFooter({ item }: { item: StrategyListItem }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", marginTop: "var(--space-4)", alignItems: "center" }}>
      {item.assigned_presets.length > 0 ? (
        item.assigned_presets.map((p) => (
          <span key={p} style={tagStyle}>{p}</span>
        ))
      ) : (
        <span style={{ ...tagStyle, color: "var(--muted)", borderColor: "var(--border)" }}>未分配预设</span>
      )}
      {item.has_backtest_adapter && (
        <span style={{ ...tagStyle, color: "var(--accent)", borderColor: "color-mix(in srgb, var(--accent) 30%, transparent)" }}>可回测</span>
      )}
    </div>
  );
}

function StatusPill({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <span style={pillStyle("var(--market-up)", "color-mix(in srgb, var(--market-up) 14%, transparent)")}>启用</span>
  ) : (
    <span style={pillStyle("var(--market-down)", "color-mix(in srgb, var(--market-down) 14%, transparent)")}>停用</span>
  );
}

const cardStyle: CSSProperties = {
  background: "var(--surface-soft)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "var(--space-4)",
  display: "flex",
  flexDirection: "column",
  transition: "border-color 160ms ease, transform 160ms ease",
};

const selectStyle: CSSProperties = {
  background: "var(--surface-soft)",
  color: "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "6px 10px",
  fontSize: "var(--text-base)",
};

const tagStyle: CSSProperties = {
  fontSize: "var(--text-xs)",
  color: "var(--muted)",
  border: "1px solid var(--border)",
  borderRadius: 999,
  padding: "2px 10px",
  whiteSpace: "nowrap",
};

function pillStyle(color: string, bg: string): CSSProperties {
  return {
    fontSize: "var(--text-xs)",
    fontWeight: 600,
    color,
    background: bg,
    borderRadius: 999,
    padding: "2px 10px",
    whiteSpace: "nowrap",
  };
}

const STATUS_LABEL: Record<string, string> = { positive: "运行中·正收益", watch: "运行中·观察", pending: "待回测", no_data: "无数据" };

function fmtPct(v?: number) {
  if (v === undefined || v === null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function MetricCells({ m }: { m?: StandaloneWindowMetrics }) {
  if (!m || !m.n) return <><td>0</td><td>—</td><td>—</td><td>—</td><td>—</td></>;
  const color = (m.avg_return_pct ?? 0) >= 0 ? "var(--color-up, #c0392b)" : "var(--color-down, #27884f)";
  return (
    <>
      <td>{m.n}</td>
      <td>{m.win_rate?.toFixed(1)}%</td>
      <td style={{ color, fontWeight: 600 }}>{fmtPct(m.avg_return_pct)}</td>
      <td>{m.pf ?? "—"}</td>
      <td>{m.max_drawdown_pct?.toFixed(1)}</td>
    </>
  );
}

/** 每日独立运行策略的单策略口径表现（后端落盘指标，随重算自动更新）。 */
function StandaloneMetricsSection() {
  const res = useApi<StandaloneMetricsResponse>(() => api.standaloneMetrics());
  if (res.loading && !res.data) return <PageSkeleton title="单策略指标" />;
  if (res.error && !res.data) return <EmptyState title="单策略指标暂不可用" reason={String(res.error)} />;
  const d = res.data;
  if (!d) return null;
  const th: CSSProperties = { textAlign: "right", padding: "4px 8px", fontWeight: 500 };
  return (
    <section data-testid="standalone-metrics" style={{ marginBottom: "var(--space-4)" }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <strong>每日独立运行策略（{d.strategies.length}）</strong>
        <span style={{ opacity: 0.7, fontSize: 12 }}>
          近12月 {d.window?.start} ~ {d.window?.end}；近期自 {d.window?.recent_start}；更新于 {d.generated_at ?? "—"}
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: "left" }} rowSpan={2}>策略</th>
              <th style={{ ...th, textAlign: "left" }} rowSpan={2}>状态</th>
              <th style={th} colSpan={5}>近 12 个月</th>
              <th style={th} colSpan={5}>近期窗口</th>
            </tr>
            <tr>
              {["票数", "胜率", "均值", "PF", "最大回撤", "票数", "胜率", "均值", "PF", "最大回撤"].map((h, i) => <th key={i} style={th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {d.strategies.map((s) => (
              <tr key={s.strategy_id} style={{ borderTop: "1px solid var(--color-border, #eee)", textAlign: "right" }}>
                <td style={{ textAlign: "left", padding: "4px 8px" }}>{s.name}</td>
                <td style={{ textAlign: "left", padding: "4px 8px" }}>{STATUS_LABEL[s.status] ?? s.status}</td>
                <MetricCells m={s.last_12m} />
                <MetricCells m={s.recent} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ opacity: 0.6, fontSize: 11, marginTop: 4 }}>口径：{d.method}</div>
    </section>
  );
}
