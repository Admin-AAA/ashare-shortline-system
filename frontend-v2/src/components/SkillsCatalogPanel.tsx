import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SkillSummary } from "../types";
import { Panel } from "./Panel";

/**
 * 策略清单面板 —— 展示 8 个策略的名称/模式/独立性/回测胜率。
 * 填补北极星 B 表红色缺口"策略清单页"。
 * 嵌入 MetricsPage 底部，与策略胜率视图（StrategyPanel）互补：
 * StrategyPanel 显示回测数据，SkillsCatalogPanel 显示策略元信息。
 */

interface PerfSummary {
  skill_id: string;
  status: string;
  sample_count: number;
  hit_rate: number;
  avg_return: number;
  errors: string[];
}

export function SkillsCatalogPanel() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [perfMap, setPerfMap] = useState<Record<string, PerfSummary>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError("");
        const skillsData = await api.skills();
        if (cancelled) return;
        setSkills(skillsData);

        // 并发拉取每个策略的 performance-summary
        const entries = await Promise.allSettled(
          skillsData.map((s) => api.skillPerformanceSummary(s.skill_id)),
        );
        if (cancelled) return;
        const pm: Record<string, PerfSummary> = {};
        for (const e of entries) {
          if (e.status === "fulfilled" && e.value) {
            pm[e.value.skill_id] = e.value as PerfSummary;
          }
        }
        setPerfMap(pm);
      } catch (e) {
        if (!cancelled) setError((e as Error).message || "加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <Panel kicker="策略清单" title="8 策略总览">
        <div style={{ padding: "var(--space-4)", color: "var(--text-muted)", fontSize: 13 }}>
          正在加载策略清单…
        </div>
      </Panel>
    );
  }

  if (error) {
    return (
      <Panel kicker="策略清单" title="8 策略总览">
        <div style={{ padding: "var(--space-4)", color: "var(--color-down)", fontSize: 13 }}>
          {error}
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      kicker="策略清单"
      title="8 策略总览"
      actions={
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          {skills.length} 套
        </span>
      }
    >
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {skills.map((s) => {
          const perf = perfMap[s.skill_id];
          const hasPerf = perf && perf.sample_count > 0;
          const wr = hasPerf ? perf.hit_rate * 100 : null;
          const wrColor = wr == null ? "var(--text-muted)" : wr >= 60 ? "var(--color-up)" : wr >= 45 ? "var(--text-secondary)" : "var(--color-down)";

          return (
            <div
              key={s.skill_id}
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
              {/* 策略名 + ID */}
              <div>
                <div style={{ fontWeight: 600, color: "var(--ink)" }}>{s.name}</div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono, monospace)" }}>
                  {s.skill_id}
                </div>
              </div>

              {/* Agent 能力 */}
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {s.has_agent_contract ? "Agent" : "规则"}
              </div>

              {/* 回测胜率 */}
              <div style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 16, fontWeight: 600, color: wrColor }}>
                {wr == null ? "—" : `${wr.toFixed(1)}%`}
              </div>

              {/* 平均收益 */}
              <div
                style={{
                  fontFamily: "var(--font-mono, monospace)",
                  fontSize: 13,
                  color: hasPerf && perf.avg_return >= 0 ? "var(--color-up)" : hasPerf ? "var(--color-down)" : "var(--text-muted)",
                }}
              >
                {hasPerf ? `${perf.avg_return >= 0 ? "+" : ""}${perf.avg_return.toFixed(2)}%` : "—"}
              </div>
            </div>
          );
        })}
      </div>

      {/* 汇总 */}
      <div style={{ display: "flex", gap: "var(--space-6)", marginTop: "var(--space-3)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border, var(--line))" }}>
        <SummaryItem label="总推荐" value={Object.values(perfMap).reduce((s, p) => s + (p?.sample_count ?? 0), 0)} />
        <SummaryItem
          label="综合胜率"
          value={(() => {
            const totals = Object.values(perfMap).reduce(
              (acc, p) => {
                acc.n += p?.sample_count ?? 0;
                acc.w += (p?.sample_count ?? 0) * (p?.hit_rate ?? 0);
                return acc;
              },
              { n: 0, w: 0 },
            );
            return totals.n > 0 ? `${((totals.w / totals.n) * 100).toFixed(1)}%` : "—";
          })()}
          color="var(--color-up)"
        />
      </div>
    </Panel>
  );
}

function SummaryItem({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <span style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 18, fontWeight: 700, color: color || "var(--ink)" }}>
        {value}
      </span>
    </div>
  );
}
