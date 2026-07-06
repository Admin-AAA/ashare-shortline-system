import { useState } from "react";
import { ChevronDown, ChevronRight, Layers, Target } from "lucide-react";
import { api } from "../api/client";
import type { StrategyListItem, StrategyPerformance } from "../types";
import { ErrorPage, EmptyState, PageSkeleton } from "../components/StateViews";
import { useApi } from "../app/useApi";

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
  const [mode, setMode] = useState<"formal" | "experiment">("formal");
  const { data, loading, error } = useApi<StrategyListItem[]>(
    () => api.strategies(mode),
    [mode],
  );

  if (loading) return <PageSkeleton title="策略清单" hint="加载策略与胜率" rowCount={8} />;
  if (error) return <ErrorPage title="策略加载失败" message={error} />;
  if (!data || data.length === 0) {
    return <EmptyState icon={<Layers size={32} />} title="暂无策略" reason="系统尚未注册任何策略" />;
  }

  const called = data.filter((s) => s.performance);
  const dormant = data.filter((s) => !s.performance);
  const coverage = Math.round((called.length / data.length) * 100);

  return (
    <div className="panel">
      <div className="panel__header">
        <div>
          <span className="panel__kicker">策略</span>
          <h2 className="panel__title">策略清单</h2>
        </div>
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-base)" }}>
          {data.length} 个策略 · {called.length} 已调用 · 覆盖 {coverage}%
        </span>
      </div>

      {/* 口径切换 */}
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", padding: "var(--space-2) 0 var(--space-4)" }}>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-base)" }}>口径</span>
        <select value={mode} onChange={(e) => setMode(e.target.value as "formal" | "experiment")} style={selectStyle}>
          <option value="formal">formal</option>
          <option value="experiment">experiment</option>
        </select>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: "var(--space-4)" }}>
        {called.map((s) => (
          <CalledCard key={s.skill_id} item={s} />
        ))}
        {dormant.map((s) => (
          <DormantCard key={s.skill_id} item={s} />
        ))}
      </div>
    </div>
  );
}

/** 已调用策略：胜率 + 样本 + 收益，可展开看 3/5/10 日表现 */
function CalledCard({ item }: { item: StrategyListItem }) {
  const perf = item.performance as StrategyPerformance;
  const [open, setOpen] = useState(false);
  const win = perf.hit_rate;
  const winColor = win == null ? "var(--muted)" : win >= 0.6 ? "var(--market-up)" : win >= 0.5 ? "var(--market-up)" : "var(--market-down)";
  const retColor = perf.avg_return == null ? "var(--muted)" : perf.avg_return >= 0 ? "var(--market-up)" : "var(--market-down)";

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

      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginTop: "var(--space-4)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)" }}>
        <div>
          <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)", letterSpacing: "0.04em" }}>胜率</div>
          <div style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.1, color: winColor, fontFamily: "var(--font-mono)" }}>
            {win == null ? "—" : `${(win * 100).toFixed(1)}%`}
          </div>
        </div>
        <div style={{ textAlign: "right", fontSize: "var(--text-sm)", color: "var(--muted)", lineHeight: 1.7, fontFamily: "var(--font-mono)" }}>
          <div>样本 {perf.sample_count} 笔</div>
          <div>
            均收益{" "}
            <span style={{ color: retColor, fontWeight: 600 }}>
              {perf.avg_return == null ? "—" : `${perf.avg_return >= 0 ? "+" : ""}${perf.avg_return.toFixed(2)}%`}
            </span>
          </div>
        </div>
      </div>

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

      <CardFooter item={item} />
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

      <CardFooter item={item} />
    </div>
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
