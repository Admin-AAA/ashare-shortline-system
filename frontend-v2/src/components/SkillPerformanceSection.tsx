import { TrendingUp } from "lucide-react";
import { api } from "../api/client";
import type { SkillSummary } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "./StateViews";
import { useApi } from "../app/useApi";

/**
 * 策略历史表现明细 —— 从原「配置页」迁移而来。
 * 在策略驾驶舱视图（StrategiesPage）下方展示：按历史胜率排序的
 * 各策略样本量 / 胜率 / 平均收益，填补北极星「策略清单页」可见缺口。
 * 后端：GET /api/skills + /api/skills/performance-summaries。
 */
type SkillPerf = {
  skill_id: string;
  sample_count: number;
  hit_rate: number;
  avg_return: number;
};

type SkillsData = { skills: SkillSummary[]; perfs: Record<string, SkillPerf> };

async function fetchSkillsData(): Promise<SkillsData> {
  const skills = await api.skills();
  // 批量拉取所有策略绩效（1 次请求替代 N 次并发，减少 MySQL CPU 争抢）
  let perfs: Record<string, SkillPerf> = {};
  try {
    const summaries = await api.batchSkillPerformanceSummaries();
    for (const p of summaries) {
      perfs[p.skill_id] = p;
    }
  } catch {
    // 降级到逐个请求
    const entries = await Promise.all(
      skills.map(async (s) => {
        try {
          const p = await api.skillPerformanceSummary(s.skill_id);
          return [s.skill_id, p] as const;
        } catch {
          return [s.skill_id, { skill_id: s.skill_id, sample_count: 0, hit_rate: 0, avg_return: 0 }] as const;
        }
      }),
    );
    for (const [id, p] of entries) perfs[id] = p;
  }
  return { skills, perfs };
}

export function SkillPerformanceSection() {
  const { data, loading, error } = useApi<SkillsData>(fetchSkillsData, []);

  if (loading) return <PageSkeleton title="策略表现明细" hint="加载各策略历史表现" rowCount={6} />;
  if (error) return <ErrorPage title="策略表现加载失败" message={error} />;
  if (!data || data.skills.length === 0)
    return <EmptyState icon={<TrendingUp size={32} />} title="暂无策略" reason="未安装任何策略" />;

  const { skills, perfs } = data;

  // 按胜率排序：有样本的按胜率降序，无样本的排最后
  const sorted = [...skills].sort((a, b) => {
    const pa = perfs[a.skill_id];
    const pb = perfs[b.skill_id];
    const na = pa?.sample_count ?? 0;
    const nb = pb?.sample_count ?? 0;
    if (na === 0 && nb === 0) return 0;
    if (na === 0) return 1;
    if (nb === 0) return -1;
    return (pb?.hit_rate ?? 0) - (pa?.hit_rate ?? 0);
  });

  const total = skills.length;
  const calledSkills = Object.values(perfs).filter((p) => p.sample_count > 0).length;

  return (
    <div style={{ marginTop: "var(--space-6)" }}>
      <div className="panel__header" style={{ marginTop: "var(--space-2)" }}>
        <span className="panel__kicker">明细</span>
        <h3 className="panel__title" style={{ fontSize: "var(--text-xl)" }}>各策略历史表现</h3>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          {calledSkills}/{total} 有样本
        </span>
      </div>

      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        {sorted.map((s) => {
          const p = perfs[s.skill_id];
          const n = p?.sample_count ?? 0;
          const wr = n > 0 ? p!.hit_rate * 100 : 0;
          const avg = p?.avg_return ?? 0;
          const hasSample = n > 0;
          const wrColor = !hasSample
            ? "var(--text-muted)"
            : wr >= 60
              ? "var(--color-up)"
              : wr >= 45
                ? "var(--text-secondary)"
                : "var(--color-down)";
          return (
            <div
              key={s.skill_id}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto auto auto",
                gap: "var(--space-4)",
                alignItems: "center",
                padding: "var(--space-3) var(--space-4)",
                background: "var(--surface-1)",
                borderRadius: 8,
              }}
            >
              <div>
                <div style={{ fontWeight: 600 }}>{s.name}</div>
                <div style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>
                  {s.skill_id}
                  {s.assigned_presets.length > 0 && ` · ${s.assigned_presets.join(" / ")}`}
                </div>
              </div>
              <Metric label="样本" value={hasSample ? String(n) : "—"} />
              <Metric label="胜率" value={hasSample ? `${wr.toFixed(1)}%` : "—"} color={wrColor} />
              <Metric
                label="平均"
                value={hasSample ? `${avg >= 0 ? "+" : ""}${avg.toFixed(2)}%` : "—"}
                color={hasSample ? (avg >= 0 ? "var(--color-up)" : "var(--color-down)") : "var(--text-muted)"}
              />
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: "var(--space-4)",
          padding: "var(--space-3)",
          background: "var(--surface-2)",
          borderRadius: 6,
          fontSize: "var(--text-sm)",
          color: "var(--text-muted)",
          display: "flex",
          gap: "var(--space-2)",
          alignItems: "center",
        }}
      >
        <TrendingUp size={14} />
        按历史胜率排序；样本量 {'<'} 10 的策略结果不稳健，仅供参考。
      </div>
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
          fontSize: "var(--text-lg)",
          fontWeight: 600,
          color: color || "var(--text-primary)",
        }}
      >
        {value}
      </div>
    </div>
  );
}
