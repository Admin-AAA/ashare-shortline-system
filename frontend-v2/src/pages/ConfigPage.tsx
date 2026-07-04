import { Settings2, TrendingUp } from "lucide-react";
import { api } from "../api/client";
import type { SkillSummary } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { useApi } from "../app/useApi";

/**
 * 配置页 —— 策略清单 + 各策略历史表现
 * 填补北极星"策略清单页"用户可见缺口。
 * 后端已有 GET /api/skills + /api/skills/{id}/performance-summary。
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
  // 并发拉每个策略的绩效
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
  const perfs: Record<string, SkillPerf> = {};
  for (const [id, p] of entries) perfs[id] = p;
  return { skills, perfs };
}

export function ConfigPage() {
  const { data, loading, error } = useApi<SkillsData>(fetchSkillsData, []);
  const skills = data?.skills ?? null;
  const perfs = data?.perfs ?? {};

  if (loading) return <PageSkeleton title="策略配置" hint="加载策略清单与历史表现" rowCount={6} />;
  if (error) return <ErrorPage title="策略加载失败" message={error} />;
  if (!skills || skills.length === 0)
    return <EmptyState icon={<Settings2 size={32} />} title="暂无策略" reason="未安装任何策略" />;

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
  const active = skills.filter((s) => s.mode === "formal").length;
  const calledSkills = Object.values(perfs).filter((p) => p.sample_count > 0).length;

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">配置</span>
        <h2 className="panel__title">策略清单</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          {active}/{total} 正式 · {calledSkills}/{total} 有样本
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
                  {s.skill_id} · {s.mode}
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
