import { useApi } from "../app/useApi";
import { api } from "../api/client";
import type {
  FilterEffectiveness,
  PrecisionAtKRow,
  ScoreLineStatsRow,
  StrategyStatsRow,
  SetupStatsRow,
} from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { SkillsCatalogPanel } from "../components/SkillsCatalogPanel";
import { LineChart } from "lucide-react";

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
  const { data: scoreLines, loading: l1, error: e1 } = useApi<ScoreLineStatsRow[]>(
    () => api.metricsByScoreLine("formal"),
    [],
  );
  const { data: strategies, loading: l2, error: e2 } = useApi<StrategyStatsRow[]>(
    () => api.metricsByStrategy("formal"),
    [],
  );
  const { data: setups, loading: l3, error: e3 } = useApi<SetupStatsRow[]>(
    () => api.metricsBySetup("formal"),
    [],
  );
  const { data: precisionAtK, loading: l4, error: e4 } = useApi<PrecisionAtKRow[]>(
    () => api.precisionAtK("formal"),
    [],
  );
  const { data: filterEffect, loading: l5, error: e5 } = useApi<FilterEffectiveness>(
    () => api.filterEffectiveness("formal"),
    [],
  );

  const loading = l1 || l2 || l3 || l4 || l5;
  const error = e1 || e2 || e3 || e4 || e5;

  if (loading) return <PageSkeleton title="指标分析" hint="加载推荐质量数据" rowCount={6} />;
  if (error) return <ErrorPage title="指标加载失败" message={error} />;
  if ((!scoreLines || scoreLines.length === 0) && (!strategies || strategies.length === 0))
    return <EmptyState icon={<LineChart size={32} />} title="暂无指标数据" reason="还没有足够的推荐 outcome" />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <PrecisionPanel rows={precisionAtK ?? []} />
      <FilterEffectivenessPanel data={filterEffect} />
      <ScoreLinePanel rows={scoreLines ?? []} />
      <StrategyPanel rows={strategies ?? []} />
      <SetupPanel rows={setups ?? []} />
      <SkillsCatalogPanel />
    </div>
  );
}

function FilterEffectivenessPanel({ data }: { data: FilterEffectiveness | null }) {
  if (!data) return null;
  const kept = data.kept;
  const rules = Object.entries(data.filtered_by_rule || {});
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">过滤效果</span>
        <h2 className="panel__title">规则有效性</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          保留 {kept.count} 笔 WR {kept.net_win_rate.toFixed(1)}% · 过滤 {data.filtered_count} 笔（{data.filter_rate_pct.toFixed(1)}%）
        </span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {rules.map(([rule, stats]) => {
          const wr = stats.win_rate;
          const isMisfilter = wr >= kept.net_win_rate; // 过滤掉的胜率 >= 保留 = 误杀
          const color = wr >= 60 ? "var(--color-up)" : wr >= 45 ? "var(--text-secondary)" : "var(--color-down)";
          return (
            <div
              key={rule}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto auto auto",
                gap: "var(--space-4)",
                alignItems: "center",
                padding: "var(--space-2) var(--space-4)",
                background: "var(--surface-1)",
                borderRadius: 6,
                border: isMisfilter ? "1px solid var(--color-up)" : "none",
              }}
            >
              <div style={{ fontWeight: 600, color: "var(--ink)" }}>
                {rule}
                {isMisfilter && (
                  <span style={{ marginLeft: 8, fontSize: 11, color: "var(--color-up)" }}>⚠ 误杀</span>
                )}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{stats.count} 笔</div>
              <div style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 16, fontWeight: 600, color }}>
                {(wr * 100).toFixed(1)}%
              </div>
              <div
                style={{
                  fontFamily: "var(--font-mono, monospace)",
                  fontSize: 13,
                  color: stats.avg_return >= 0 ? "var(--color-up)" : "var(--color-down)",
                }}
              >
                {stats.avg_return >= 0 ? "+" : ""}{stats.avg_return.toFixed(2)}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PrecisionPanel({ rows }: { rows: PrecisionAtKRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">排序质量</span>
        <h2 className="panel__title">Precision@K</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          前 K 推荐准确率（越靠前应越高）
        </span>
      </div>
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
    </div>
  );
}

function ScoreLinePanel({ rows }: { rows: ScoreLineStatsRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">分析</span>
        <h2 className="panel__title">分数段胜率</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          推荐分数线 ≥ X 的累计胜率
        </span>
      </div>
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
    </div>
  );
}

function StrategyPanel({ rows }: { rows: StrategyStatsRow[] }) {
  if (rows.length === 0) return null;
  const sorted = [...rows].sort((a, b) => b.hit_rate - a.hit_rate);
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">分析</span>
        <h2 className="panel__title">策略胜率</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          按胜率降序
        </span>
      </div>
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
    </div>
  );
}

function SetupPanel({ rows }: { rows: SetupStatsRow[] }) {
  if (rows.length === 0) return null;
  const sorted = [...rows].sort((a, b) => b.hit_rate - a.hit_rate);
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">分析</span>
        <h2 className="panel__title">Setup 胜率</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          按胜率降序
        </span>
      </div>
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
    </div>
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
