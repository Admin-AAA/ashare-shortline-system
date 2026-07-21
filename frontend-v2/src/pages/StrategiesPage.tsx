import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, FileText, History, Layers, Target } from "lucide-react";
import { api } from "../api/client";
import type { DateRangeMeta, PresetStatsRow, StrategyListItem, StrategyPerformance } from "../types";
import { ErrorPage, EmptyState, PageSkeleton } from "../components/StateViews";
import { Panel } from "../components/Panel";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";

/** 时间窗口选项：null=全部历史累计，N=最近 N 天且已结算。与指标页保持一致。 */
const WINDOW_OPTIONS: Array<{ label: string; value: number | null }> = [
  { label: "全部", value: null },
  { label: "近 30 天", value: 30 },
  { label: "近 90 天", value: 90 },
];

/**
 * 策略清单页 —— 系统全部策略的"驾驶舱视图"。
 *
 * 一个页面看全 8 个策略：中文名 + 简介 + 启用状态 + 调用覆盖 + 胜率。
 * 直接填补北极星"策略清单页"缺口（8 策略 + 简介 + 回测胜率），
 * 并把"覆盖 3/8 → 8/8"的差距显式呈现：已调用的按胜率排序在前，从未调用的沉底并标注"未覆盖"。
 *
 * 设计：克制优先。bold 只落在胜率一个数字上；其余保持安静。A 股语义：涨/胜=红，跌/亏=绿，低饱和。
 */

const HORIZONS = [
  { key: "3", label: "3日" },
  { key: "5", label: "5日" },
  { key: "10", label: "10日" },
];

export function StrategiesPage() {
  const [tab, setTab] = useState<"skills" | "presets">("skills");
  const [windowDays, setWindowDays] = useState<number | null>(null);
  const skills = useApi<StrategyListItem[]>(
    () => api.strategies(windowDays ?? undefined),
    [windowDays],
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
      {tab === "skills" ? (
        <SkillsView skills={skills} windowDays={windowDays} setWindowDays={setWindowDays} />
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
  setWindowDays,
}: {
  skills: { data: StrategyListItem[] | null; loading: boolean; error: string };
  windowDays: number | null;
  setWindowDays: (v: number | null) => void;
}) {
  if (skills.loading) return <PageSkeleton title="策略清单" hint="加载策略与胜率" rowCount={8} />;
  if (skills.error) return <ErrorPage title="策略加载失败" message={skills.error} />;
  const data = skills.data;
  if (!data || data.length === 0) {
    return <EmptyState icon={<Layers size={32} />} title="暂无策略" reason="系统尚未注册任何策略" />;
  }

  const called = data.filter((s) => s.performance);
  const dormant = data.filter((s) => !s.performance);
  const coverage = Math.round((called.length / data.length) * 100);

  return (
    <>
      {/* 时间窗口 */}
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-2) 0 var(--space-4)", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
          <span style={{ color: "var(--muted)", fontSize: "var(--text-base)", marginRight: "var(--space-1)" }}>时间窗口</span>
          {WINDOW_OPTIONS.map((opt) => {
            const active = windowDays === opt.value;
            return (
              <button
                key={String(opt.value)}
                onClick={() => setWindowDays(opt.value)}
                style={{
                  padding: "4px 10px",
                  fontSize: 12,
                  borderRadius: 4,
                  border: "1px solid var(--border)",
                  background: active ? "var(--accent, #2563eb)" : "var(--surface-soft)",
                  color: active ? "#fff" : "var(--muted)",
                  cursor: "pointer",
                  fontWeight: active ? 600 : 400,
                }}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
        <span style={{ color: "var(--muted)", fontSize: 11 }}>
          {(() => {
            const dr: DateRangeMeta | undefined = called.find((s) => s.performance?.date_range)?.performance?.date_range;
            if (dr && dr.window_days) {
              return `窗口 ${dr.start_date} ~ ${dr.end_date}（已结算截止 ${dr.settle_cutoff}，已结算 ${dr.labeled_in_window} 笔）`;
            }
            return "统计范围：全部历史累计";
          })()}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: "var(--space-4)" }}>
        {called.map((s) => (
          <CalledCard key={s.skill_id} item={s} />
        ))}
        {dormant.map((s) => (
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
                <td style={{ padding: "var(--space-3) var(--space-3)", color: "var(--muted)" }}>{r.run_count}</td>
                <td style={{ padding: "var(--space-3) var(--space-3)", color: lowSample ? "var(--warning, #b45309)" : "var(--muted)" }}>
                  {r.recommendation_count}
                  {lowSample ? " ⚠" : ""}
                </td>
                <td style={{ padding: "var(--space-3) var(--space-3)", fontWeight: 700, color: winColor }}>
                  {win == null ? "—" : `${(win * 100).toFixed(1)}%`}
                </td>
                <td style={{ padding: "var(--space-3) var(--space-3)", fontWeight: 600, color: retColor }}>
                  {ret == null ? "—" : `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%`}
                </td>
                <td style={{ padding: "var(--space-3) var(--space-3)", color: "var(--muted)" }}>
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

/** 已调用策略：胜率 + 样本 + 收益，可展开看 3/5/10 日表现 */
function CalledCard({ item }: { item: StrategyListItem }) {
  const perf = item.performance as StrategyPerformance;
  const { navigate } = useUrlRoute();
  const [open, setOpen] = useState(false);
  const win = perf.hit_rate;
  const winColor = win == null ? "var(--muted)" : win >= 0.6 ? "var(--market-up)" : win >= 0.5 ? "var(--market-up)" : "var(--market-down)";
  const retColor = perf.avg_return == null ? "var(--muted)" : perf.avg_return >= 0 ? "var(--market-up)" : "var(--market-down)";
  const pf = perf.profit_loss_ratio;
  const pfColor = pf == null ? "var(--muted)" : pf >= 1.4 ? "var(--market-up)" : pf >= 1.0 ? "var(--text)" : "var(--market-down)";
  // 统计可信度：后端 reliable=false（样本<20）时胜率属噪声，须显式提示，防过度信任
  const lowSample = !perf.reliable;
  // 最新补跑门线明细（T27：与 self_check gate_blocked 同源）
  const gate = item.last_exp_gate ?? perf.last_exp_gate;
  // 最近命中交易日（YYYY-MM-DD），北极星 C 项：策略可追溯
  const lastDate = perf.last_recommendation_date;

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

      {/* 核心三指标等权：胜率 / 盈亏比 / 均收益。
          克制原则——不让胜率独占 hero：盈亏比 <1 的高胜率策略风险调整后并不优，
          skill 明确「不要只盯胜率，判 regime 必须看盈亏比」。三个 D 项指标平级、右对齐、等宽字体。 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "var(--space-3)", marginTop: "var(--space-4)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)" }}>
        <Metric label="胜率" value={win == null ? "—" : `${(win * 100).toFixed(1)}%`} color={winColor} />
        <Metric label="盈亏比" value={pf == null ? "—" : pf.toFixed(2)} color={pfColor} />
        <Metric
          label="均收益"
          value={perf.avg_return == null ? "—" : `${perf.avg_return >= 0 ? "+" : ""}${perf.avg_return.toFixed(2)}%`}
          color={retColor}
        />
      </div>
      <div style={{ marginTop: "var(--space-2)", fontSize: "var(--text-xs)", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
        样本 {perf.sample_count} 笔 · 最近命中{" "}
        <span style={{ color: lastDate ? "var(--text)" : "var(--muted)" }}>{lastDate || "—"}</span>
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
            <span>样本不足（n={perf.sample_count}）· 胜率统计仅供参考</span>
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
        {open ? "收起" : "多周期表现"}
      </button>

      {open && (
        <div style={{ marginTop: "var(--space-3)", display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "var(--space-2)" }}>
          {HORIZONS.map((h) => {
            const m = (perf.horizon_metrics as Record<string, any> | undefined)?.[h.key];
            const hWin = m?.hit_rate;
            const hRet = m?.average_executed_return;
            const c = hRet == null ? "var(--muted)" : hRet >= 0 ? "var(--market-up)" : "var(--market-down)";
            return (
              <div key={h.key} style={{ background: "var(--surface)", borderRadius: 8, padding: "var(--space-2)", textAlign: "center" }}>
                <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)" }}>{h.label}</div>
                <div style={{ fontSize: "var(--text-base)", fontWeight: 650, fontFamily: "var(--font-mono)", color: hWin == null ? "var(--muted)" : hWin >= 0.5 ? "var(--market-up)" : "var(--market-down)" }}>
                  {hWin == null ? "—" : `${(hWin * 100).toFixed(0)}%`}
                </div>
                <div style={{ fontSize: "var(--text-xs)", fontFamily: "var(--font-mono)", color: c }}>
                  {hRet == null ? "—" : `${hRet >= 0 ? "+" : ""}${hRet.toFixed(1)}%`}
                </div>
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

/** 从未被调用的策略：明确标注"未覆盖"，不伪装成有数据 */
function DormantCard({ item }: { item: StrategyListItem }) {
  return (
    <div className="strat-card" style={{ ...cardStyle, opacity: 0.82 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--space-2)" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 650, fontSize: "var(--text-lg)", color: "var(--text)", lineHeight: 1.2 }}>{item.name}</div>
          <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginTop: 2, fontFamily: "var(--font-mono)" }}>{item.skill_id}</div>
        </div>
        <span style={pillStyle("var(--muted)", "color-mix(in srgb, var(--muted) 14%, transparent)")}>未覆盖</span>
      </div>

      <p style={{ margin: "var(--space-3) 0 0", color: "var(--muted)", fontSize: "var(--text-sm)", lineHeight: 1.5, minHeight: 38 }}>
        {item.description || "（无简介）"}
      </p>

      <div style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: "var(--space-2)", color: "var(--muted)", fontSize: "var(--text-sm)" }}>
        <Target size={14} />
        已注册，但推荐管线尚未触发此策略
      </div>

      <StrategyDetailLink item={item} />
      <CardFooter item={item} />
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

const cardStyle: React.CSSProperties = {
  background: "var(--surface-soft)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "var(--space-4)",
  display: "flex",
  flexDirection: "column",
  transition: "border-color 160ms ease, transform 160ms ease",
};

const selectStyle: React.CSSProperties = {
  background: "var(--surface-soft)",
  color: "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "6px 10px",
  fontSize: "var(--text-base)",
};

const tagStyle: React.CSSProperties = {
  fontSize: "var(--text-xs)",
  color: "var(--muted)",
  border: "1px solid var(--border)",
  borderRadius: 999,
  padding: "2px 10px",
  whiteSpace: "nowrap",
};

function pillStyle(color: string, bg: string): React.CSSProperties {
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
