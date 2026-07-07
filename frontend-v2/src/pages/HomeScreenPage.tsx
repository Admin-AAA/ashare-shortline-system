import { ArrowUpRight, BarChart3, Compass, History, Layers, LayoutGrid, LineChart } from "lucide-react";
import { useUrlRoute } from "../app/useUrlRoute";
import { useApi } from "../app/useApi";
import { api } from "../api/client";
import type { HomeBootstrap, MarketSnapshot, RunSummary } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";

/**
 * 首页（着陆页）—— 短线交易驾驶舱入口。
 * 接通 /api/home/bootstrap：实时市场宽度 + 最近一次推荐 run + 策略开关状态，
 * 让用户在打开系统的第一屏就能判断「今天市场温度」与「系统最近产出」，并一键进入工作区。
 * 设计遵循 T7 审美硬约束：暗色、大量留白、涨红跌绿低饱和、数字等宽对齐、bold 只落在关键数字。
 */
export function HomePage() {
  const { navigateSection } = useUrlRoute();
  const { data, loading, error } = useApi<HomeBootstrap>(() => api.bootstrap(), []);

  if (loading) return <PageSkeleton title="驾驶舱" hint="加载市场概览" rowCount={4} />;
  if (error) return <ErrorPage title="市场概览加载失败" message={error} />;

  const snap = data?.market_snapshot;
  const formal = data?.recent_runs.formal ?? null;
  const experiment = data?.recent_runs.experiment ?? null;
  const latest = formal ?? experiment;
  const skills = data?.skill_summary;

  return (
    <div style={landingStyle}>
      {/* 头部：系统名 + 市场一句话 */}
      <header style={heroStyle}>
        <div>
          <h1 style={heroTitleStyle}>短线交易驾驶舱</h1>
          <p style={heroSubStyle}>
            {snap?.trade_date ?? "—"} · {breadthLine(snap)}
          </p>
        </div>
        {latest && <RunPill run={latest} onClick={() => navigateSection("dashboard")} />}
      </header>

      {/* 市场宽度（核心区） */}
      <section style={panelStyle}>
        <PanelHead kicker="市场温度" title="今日涨跌家数" />
        {snap ? (
          <>
            <BreadthGrid snap={snap} />
            {snap.main_industries?.length ? <Industries chips={snap.main_industries} /> : null}
          </>
        ) : (
          <EmptyState title="暂无行情快照" reason="市场数据尚未就绪" />
        )}
      </section>

      {/* 最近产出 + 快捷入口 */}
      <div style={gridStyle}>
        <section style={panelStyle}>
          <PanelHead kicker="最近产出" title="最新推荐 run" />
          <LatestRun run={latest} onOpen={() => navigateSection("dashboard")} />
        </section>

        <section style={panelStyle}>
          <PanelHead kicker="工作区" title="快速进入" />
          <NavGrid onNavigate={navigateSection} />
        </section>
      </div>

      {/* 策略开关状态 */}
      {skills && (
        <section style={{ ...panelStyle, padding: "var(--space-3) var(--space-4)" }}>
          <SkillStatus s={skills} />
        </section>
      )}
    </div>
  );
}

/* ---------- 头部 ---------- */

function RunPill({ run, onClick }: { run: RunSummary; onClick: () => void }) {
  return (
    <button onClick={onClick} style={pillStyle} title="查看驾驶舱">
      <span style={{ color: "var(--muted)", fontSize: 12 }}>{run.run_mode === "formal" ? "正式" : "实验"}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--ink)" }}>
        {run.trade_date} · {run.recommended_count} 只
      </span>
      <ArrowUpRight size={14} color="var(--muted)" />
    </button>
  );
}

/* ---------- 市场宽度 ---------- */

function BreadthGrid({ snap }: { snap: MarketSnapshot }) {
  const up = snap.up_count;
  const down = snap.down_count;
  const total = up + down;
  const upPct = total > 0 ? (up / total) * 100 : 50;

  const metrics: { label: string; value: number; color: string }[] = [
    { label: "上涨", value: up, color: "var(--market-up)" },
    { label: "下跌", value: down, color: "var(--market-down)" },
    { label: "涨停", value: snap.limit_up_count, color: "var(--market-up)" },
    { label: "跌停", value: snap.limit_down_count, color: "var(--market-down)" },
  ];

  return (
    <div>
      <div style={breadthGridStyle}>
        {metrics.map((m) => (
          <div key={m.label} style={metricCellStyle}>
            <div style={{ fontSize: 12, color: "var(--muted)" }}>{m.label}</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 28, fontWeight: 600, color: m.color, lineHeight: 1.1 }}>
              {m.value}
            </div>
          </div>
        ))}
      </div>
      {/* 涨跌比条：红=涨 绿=跌 */}
      <div style={barTrackStyle}>
        <div style={{ ...barFillStyle, width: `${upPct}%`, background: "var(--market-up)" }} />
        <div style={{ ...barFillStyle, width: `${100 - upPct}%`, background: "var(--market-down)" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
        <span>涨跌比 {(total > 0 ? (up / Math.max(1, down)).toFixed(2) : "—")}</span>
        <span>样本 {snap.sample_count} 只</span>
      </div>
    </div>
  );
}

function Industries({ chips }: { chips: string[] }) {
  return (
    <div style={{ marginTop: "var(--space-3)" }}>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>领涨板块</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {chips.map((c) => (
          <span key={c} style={chipStyle}>
            {c}
          </span>
        ))}
      </div>
    </div>
  );
}

/* ---------- 最近 run ---------- */

function LatestRun({ run, onOpen }: { run: RunSummary | null; onOpen: () => void }) {
  if (!run) {
    return <EmptyState title="暂无推荐记录" reason="系统尚未运行过推荐" />;
  }
  const statusLabel =
    run.status === "completed" ? "已完成" : run.status === "running" ? "运行中" : run.status === "failed" ? "失败" : run.status;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, color: "var(--muted)" }}>{run.trade_date}</span>
        <span style={statusChipStyle(run.status)}>{statusLabel}</span>
      </div>
      <div style={runStatsStyle}>
        <RunStat label="推荐数" value={String(run.recommended_count)} />
        <RunStat label="命中率" value={run.hit_rate > 0 ? `${run.hit_rate.toFixed(1)}%` : "—"} />
        <RunStat label="分数线" value={run.score_line.toFixed(0)} />
      </div>
      <button onClick={onOpen} style={ghostBtnStyle}>
        查看驾驶舱 <ArrowUpRight size={14} />
      </button>
    </div>
  );
}

function RunStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{label}</div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 18, fontWeight: 600, color: "var(--ink)" }}>{value}</div>
    </div>
  );
}

/* ---------- 快捷导航 ---------- */

const NAV_DEFS: { section: "dashboard" | "pool" | "history" | "strategies" | "metrics" | "matrix"; label: string; icon: typeof Compass }[] = [
  { section: "dashboard", label: "驾驶舱", icon: Compass },
  { section: "pool", label: "候选池", icon: BarChart3 },
  { section: "history", label: "复盘", icon: History },
  { section: "strategies", label: "策略", icon: Layers },
  { section: "metrics", label: "指标", icon: LineChart },
  { section: "matrix", label: "矩阵", icon: LayoutGrid },
];

function NavGrid({ onNavigate }: { onNavigate: (s: "dashboard" | "pool" | "history" | "strategies" | "metrics" | "matrix") => void }) {
  return (
    <div style={navGridStyle}>
      {NAV_DEFS.map(({ section, label, icon: Icon }) => (
        <button key={section} onClick={() => onNavigate(section)} style={navCardStyle}>
          <Icon size={18} color="var(--accent)" />
          <span style={{ fontSize: 14, color: "var(--ink)" }}>{label}</span>
          <ArrowUpRight size={13} color="var(--muted)" style={{ marginLeft: "auto" }} />
        </button>
      ))}
    </div>
  );
}

/* ---------- 策略开关 ---------- */

function SkillStatus({ s }: { s: { formal_count: number; experiment_count: number; disabled_count: number } }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-4)", fontSize: 13, color: "var(--muted)" }}>
      <span style={{ color: "var(--ink)", fontWeight: 600 }}>策略开关</span>
      <span>
        正式 <b style={{ color: "var(--market-up)", fontFamily: "var(--font-mono)" }}>{s.formal_count}</b>
      </span>
      <span>
        实验 <b style={{ color: "var(--ink)", fontFamily: "var(--font-mono)" }}>{s.experiment_count}</b>
      </span>
      <span>
        停用 <b style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>{s.disabled_count}</b>
      </span>
    </div>
  );
}

/* ---------- 小部件 ---------- */

function PanelHead({ kicker, title }: { kicker: string; title: string }) {
  return (
    <div style={{ marginBottom: "var(--space-3)" }}>
      <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--muted)" }}>{kicker}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color: "var(--ink)" }}>{title}</div>
    </div>
  );
}

function breadthLine(snap?: MarketSnapshot | null): string {
  if (!snap) return "行情快照未就绪";
  const total = snap.up_count + snap.down_count;
  if (total === 0) return "涨跌家数持平";
  const upPct = (snap.up_count / total) * 100;
  if (upPct >= 60) return "普涨 · 情绪偏暖";
  if (upPct <= 40) return "普跌 · 情绪偏冷";
  return "分化 · 多空拉锯";
}

/* ---------- 样式 ---------- */

const landingStyle: React.CSSProperties = {
  maxWidth: 960,
  margin: "0 auto",
  padding: "var(--space-6) var(--space-4)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-4)",
};

const heroStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-4)",
  flexWrap: "wrap",
};

const heroTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 26,
  fontWeight: 700,
  color: "var(--ink)",
  letterSpacing: "-0.01em",
};

const heroSubStyle: React.CSSProperties = {
  margin: "6px 0 0",
  fontSize: 13,
  color: "var(--muted)",
  fontFamily: "var(--font-mono)",
};

const pillStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  background: "var(--surface-soft)",
  border: "1px solid var(--border)",
  borderRadius: 999,
  padding: "6px 12px",
  cursor: "pointer",
};

const panelStyle: React.CSSProperties = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "var(--space-4)",
};

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: "var(--space-4)",
};

const breadthGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(4, 1fr)",
  gap: "var(--space-3)",
};

const metricCellStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const barTrackStyle: React.CSSProperties = {
  display: "flex",
  height: 8,
  borderRadius: 4,
  overflow: "hidden",
  background: "var(--surface-soft)",
  marginTop: "var(--space-3)",
};

const barFillStyle: React.CSSProperties = {
  height: "100%",
  transition: "width 0.3s ease",
};

const chipStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "3px 10px",
  borderRadius: 6,
  background: "var(--surface-soft)",
  border: "1px solid var(--border)",
  color: "var(--ink)",
};

const runStatsStyle: React.CSSProperties = {
  display: "flex",
  gap: "var(--space-4)",
};

const ghostBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  alignSelf: "flex-start",
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "6px 12px",
  fontSize: 13,
  color: "var(--ink)",
  cursor: "pointer",
};

const navGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: "var(--space-2)",
};

const navCardStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  background: "var(--surface-soft)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: "var(--space-3)",
  cursor: "pointer",
  textAlign: "left",
  transition: "border-color 0.15s ease",
};

const statusChipStyle = (status: string): React.CSSProperties => ({
  fontSize: 11,
  padding: "2px 8px",
  borderRadius: 4,
  fontFamily: "var(--font-mono)",
  color: status === "completed" ? "var(--market-up)" : status === "failed" ? "var(--market-down)" : "var(--muted)",
  border: `1px solid ${status === "completed" ? "var(--market-up)" : status === "failed" ? "var(--market-down)" : "var(--border)"}`,
  background: "transparent",
});
