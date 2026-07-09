import {
  BarChart3,
  Flame,
  History,
  Layers,
  LayoutGrid,
  LineChart,
  ListChecks,
} from "lucide-react";
import type { DashboardOverview, MarketSnapshot, RunSummary } from "../types";
import { CollapsiblePanel } from "./CollapsiblePanel";
import { EmptyState } from "./StateViews";
import { RunModeBadge } from "./RunModeBadge";

/** HomePage 共享面板组件 —— 从 HomePage.tsx 抽离，消除主文件过大问题。 */

export function Sep() {
  return <span style={{ color: "var(--line-strong)" }}>·</span>;
}

export function Metric({
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

export function regimeToLabel(regime: string): string {
  const map: Record<string, string> = {
    strong_bull: "强势·牛市",
    bull: "牛市",
    neutral: "震荡",
    bear: "熊市",
    strong_bear: "强势·熊市",
    climax: "高潮",
    "强势": "强势·牛市",
    "牛市": "牛市",
    "震荡": "震荡",
    "熊市": "熊市",
    "弱势": "弱势·熊市",
  };
  return map[regime] ?? regime ?? "—";
}

// ─── 市场温度面板（合并风险雷达） ─────────────────────────────
export function MarketTempPanel({
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
    <CollapsiblePanel
      kicker="市场温度"
      title={
        <>
          <Flame size={16} style={{ color: isHot ? "var(--warning)" : "var(--muted)" }} />
          {regimeLabel}
        </>
      }
      actions={<div className="text-mono text-muted" style={{ fontSize: "var(--text-xs)" }}>{market.trade_date}</div>}
    >
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
    </CollapsiblePanel>
  );
}

// ─── 最近产出卡（合并自着陆页 LatestRun，展示命中率 / 分数线） ─────────
export function LatestRunCard({
  run,
  onOpen,
}: {
  run: RunSummary | null;
  onOpen: () => void;
}) {
  if (!run) {
    return <EmptyState title="暂无推荐记录" reason="系统尚未运行过推荐" />;
  }
  const statusLabel =
    run.status === "completed" ? "已完成" : run.status === "running" ? "运行中" : run.status === "failed" ? "失败" : run.status;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, color: "var(--muted)", display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          {run.trade_date}
          <RunModeBadge runMode={run.run_mode} />
        </span>
        <span
          style={{
            fontSize: 11,
            padding: "2px 8px",
            borderRadius: 4,
            fontFamily: "var(--font-mono)",
            color: run.status === "completed" ? "var(--market-up)" : run.status === "failed" ? "var(--market-down)" : "var(--muted)",
            border: `1px solid ${run.status === "completed" ? "var(--market-up)" : run.status === "failed" ? "var(--market-down)" : "var(--line)"}`,
            background: "transparent",
          }}
        >
          {statusLabel}
        </span>
      </div>
      <div style={{ display: "flex", gap: "var(--space-4)" }}>
        <RunStat label="推荐数" value={String(run.recommended_count)} />
        <RunStat label="命中率" value={run.hit_rate > 0 ? `${run.hit_rate.toFixed(1)}%` : "—"} />
        <RunStat label="分数线" value={run.score_line.toFixed(0)} />
      </div>
      <button onClick={onOpen} className="btn btn--ghost" style={{ alignSelf: "flex-start" }}>
        查看驾驶舱
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

// ─── 工作区快捷入口（合并自着陆页 NavGrid） ─────────────────────
type QuickNavSection = "pool" | "history" | "records" | "matrix" | "metrics" | "strategies";

const QUICK_NAV: { section: QuickNavSection; label: string; icon: typeof BarChart3 }[] = [
  { section: "pool", label: "候选池", icon: BarChart3 },
  { section: "history", label: "复盘", icon: History },
  { section: "records", label: "记录", icon: ListChecks },
  { section: "matrix", label: "矩阵", icon: LayoutGrid },
  { section: "metrics", label: "指标", icon: LineChart },
  { section: "strategies", label: "策略", icon: Layers },
];

export function QuickNavGrid({
  onNavigate,
}: {
  onNavigate: (s: QuickNavSection) => void;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
        gap: "var(--space-2)",
      }}
    >
      {QUICK_NAV.map(({ section, label, icon: Icon }) => (
        <button key={section} onClick={() => onNavigate(section)} className="quick-nav-card">
          <Icon size={18} className="quick-nav-card__icon" />
          <span className="quick-nav-card__label">{label}</span>
        </button>
      ))}
    </div>
  );
}

// ─── 策略开关状态（合并自着陆页 SkillStatus） ───────────────────
export function SkillStatusStrip({
  summary,
}: {
  summary: { enabled_count: number; disabled_count: number };
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-4)", fontSize: 13, color: "var(--muted)", flexWrap: "wrap" }}>
      <span style={{ color: "var(--ink)", fontWeight: 600 }}>策略开关</span>
      <span>
        启用 <b style={{ color: "var(--market-up)", fontFamily: "var(--font-mono)" }}>{summary.enabled_count}</b>
      </span>
      <span>
        停用 <b style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>{summary.disabled_count}</b>
      </span>
    </div>
  );
}

// ─── 市场宽度（合并自着陆页 BreadthGrid：上涨/下跌原始家数 + 涨跌比条 + 样本 + 领涨板块） ──
export function MarketBreadthPanel({ snapshot }: { snapshot: MarketSnapshot }) {
  const up = snapshot.up_count;
  const down = snapshot.down_count;
  const total = up + down;
  const upPct = total > 0 ? (up / total) * 100 : 50;

  return (
    <div>
      <p style={{ margin: "0 0 var(--space-4)", fontSize: 12, color: "var(--muted)" }}>{breadthLine(snapshot)}</p>

      {/* 一行四数：上涨(左) | 涨停·跌停(中，左右留限) | 下跌(右) */}
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "var(--space-4)" }}>
        {/* 左：上涨 */}
        <div style={{ textAlign: "left", flex: "0 0 auto" }}>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>上涨</div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 40, fontWeight: 700, color: "var(--market-up)", lineHeight: 1.1 }}>{up}</div>
        </div>

        {/* 中：涨停 | 跌停（居中，左右各留 40px 不挤压两侧） */}
        <div style={{ display: "flex", gap: "var(--space-6)", alignItems: "flex-end", flex: "0 0 auto", margin: "0 40px" }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>涨停</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 28, fontWeight: 700, color: "var(--market-up)", lineHeight: 1.1 }}>{snapshot.limit_up_count}</div>
          </div>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>跌停</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 28, fontWeight: 700, color: "var(--market-down)", lineHeight: 1.1 }}>{snapshot.limit_down_count}</div>
          </div>
        </div>

        {/* 右：下跌 */}
        <div style={{ textAlign: "right", flex: "0 0 auto" }}>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>下跌</div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 40, fontWeight: 700, color: "var(--market-down)", lineHeight: 1.1 }}>{down}</div>
        </div>
      </div>

      {/* 涨跌比条：红=涨 绿=跌 */}
      <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", background: "var(--surface-soft)", marginTop: "var(--space-3)" }}>
        <div style={{ width: `${upPct}%`, background: "var(--market-up)", transition: "width var(--motion-normal)" }} />
        <div style={{ width: `${100 - upPct}%`, background: "var(--market-down)", transition: "width var(--motion-normal)" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
        <span>涨跌比 {(total > 0 ? (up / Math.max(1, down)).toFixed(2) : "—")}</span>
        <span>样本 {snapshot.sample_count} 只</span>
      </div>

      {snapshot.main_industries?.length ? (
        <div style={{ marginTop: "var(--space-3)" }}>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>领涨板块</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {snapshot.main_industries.map((c) => (
              <span key={c} style={{ fontSize: 12, padding: "3px 10px", borderRadius: 6, background: "var(--surface-soft)", border: "1px solid var(--line)", color: "var(--ink)" }}>
                {c}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** 市场情绪一句话（还原自着陆页 breadthLine） */
export function breadthLine(snap?: MarketSnapshot | null): string {
  if (!snap) return "行情快照未就绪";
  const total = snap.up_count + snap.down_count;
  if (total === 0) return "涨跌家数持平";
  const upPct = (snap.up_count / total) * 100;
  if (upPct >= 60) return "普涨 · 情绪偏暖";
  if (upPct <= 40) return "普跌 · 情绪偏冷";
  return "分化 · 多空拉锯";
}
