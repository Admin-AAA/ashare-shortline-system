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

// 历史过滤规则元信息：deprecated 规则已从实盘策略删除（见 strategy.yaml），
// 此处仅作复盘 + 回归护栏。注意：误杀（被砍胜率≥保留）是"有害"信号，
// 对废弃规则表示"当年因此被删除"，绝不用绿色庆祝。
const FILTER_RULE_META: Record<string, { deprecated?: boolean; note?: string; reason?: string }> = {
  avg_cost_high: {
    deprecated: true,
    reason: "avg_cost>200 会误杀科创板高价龙头（WR 76.8%），已于实盘删除",
    note: "实盘已删除（第100轮 T5）：avg_cost>200 误杀科创板高价龙头（WR 76.8%）。此处仅诊断/回归护栏，请勿重新启用",
  },
};

function FilterEffectivenessPanel({ data }: { data: FilterEffectiveness | null }) {
  if (!data) return null;
  const kept = data.kept;
  const keptWr = kept.net_win_rate;
  const rules = Object.entries(data.filtered_by_rule || {});
  const active = rules.filter(([r]) => !FILTER_RULE_META[r]?.deprecated);
  const deprecated = rules.filter(([r]) => FILTER_RULE_META[r]?.deprecated);

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">过滤效果</span>
        <h2 className="panel__title">规则有效性</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          保留 {kept.count} 笔 WR {keptWr.toFixed(1)}% · 过滤 {data.filtered_count} 笔（{data.filter_rate_pct.toFixed(1)}%）
        </span>
      </div>

      <p style={{ margin: "0 0 var(--space-3)", fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
        每行数字 = <b>被该规则剔除的交易</b>的胜率与收益。若「被砍胜率 ≥ 保留胜率（{keptWr.toFixed(1)}%）」，说明规则砍掉的是赢家 → 过度过滤（误杀）。
      </p>

      {active.length > 0 && (
        <RuleSection title="活跃规则" hint="当前实盘生效" rows={active} keptWr={keptWr} />
      )}
      {deprecated.length > 0 && (
        <RuleSection title="历史规则 · 已移出实盘" hint="仅复盘与回归护栏" rows={deprecated} keptWr={keptWr} deprecated />
      )}
    </div>
  );
}

function RuleSection({
  title,
  hint,
  rows,
  keptWr,
  deprecated,
}: {
  title: string;
  hint: string;
  rows: [string, { count: number; win_rate: number; avg_return: number }][];
  keptWr: number;
  deprecated?: boolean;
}) {
  return (
    <div style={{ marginTop: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: "var(--space-2)" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>{title}</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{hint}</span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {rows.map(([rule, stats]) => (
          <RuleRow key={rule} rule={rule} stats={stats} keptWr={keptWr} deprecated={deprecated} />
        ))}
      </div>
    </div>
  );
}

function RuleRow({
  rule,
  stats,
  keptWr,
  deprecated,
}: {
  rule: string;
  stats: { count: number; win_rate: number; avg_return: number };
  keptWr: number;
  deprecated?: boolean;
}) {
  const wr = stats.win_rate;
  const isMisfilter = wr >= keptWr;
  const wrColor = wr >= 60 ? "var(--color-up)" : wr >= 45 ? "var(--text-secondary)" : "var(--color-down)";

  let chip;
  if (deprecated) {
    chip = (
      <span
        title={FILTER_RULE_META[rule]?.note}
        style={{ fontSize: 11, padding: "1px 8px", borderRadius: 4, color: "var(--text-muted)", border: "1px solid var(--border, #ddd)", background: "var(--surface-2, #f3f3f3)", cursor: "help", whiteSpace: "nowrap" }}
      >
        已废弃
      </span>
    );
  } else if (isMisfilter) {
    chip = (
      <span
        title={`被砍 WR ${wr.toFixed(1)}% ≥ 保留 WR ${keptWr.toFixed(1)}%：规则砍掉的是赢家`}
        style={{ fontSize: 11, padding: "1px 8px", borderRadius: 4, color: "var(--color-warning, #b45309)", border: "1px solid var(--color-warning, #b45309)", background: "rgba(245,158,11,0.12)", whiteSpace: "nowrap" }}
      >
        ⚠ 过度过滤
      </span>
    );
  } else {
    chip = (
      <span
        title={`被砍 WR ${wr.toFixed(1)}% < 保留 WR ${keptWr.toFixed(1)}%：正确剔除弱势交易`}
        style={{ fontSize: 11, padding: "1px 8px", borderRadius: 4, color: "var(--color-up)", border: "1px solid var(--color-up)", background: "rgba(16,185,129,0.12)", whiteSpace: "nowrap" }}
      >
        ✓ 过滤有效
      </span>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto auto auto",
        gap: "var(--space-4)",
        alignItems: "center",
        padding: "var(--space-2) var(--space-4)",
        background: "var(--surface-1)",
        borderRadius: 6,
        border: deprecated
          ? "1px dashed var(--border, #ddd)"
          : isMisfilter
            ? "1px solid var(--color-warning, #b45309)"
            : "none",
      }}
    >
      <div style={{ fontWeight: 600, color: "var(--ink)", display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {rule}
          {chip}
        </div>
        {deprecated && FILTER_RULE_META[rule]?.reason && (
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>{FILTER_RULE_META[rule]!.reason}</span>
        )}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>被砍 {stats.count} 笔</div>
      <div style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 16, fontWeight: 600, color: wrColor }}>
        {wr.toFixed(1)}%
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
