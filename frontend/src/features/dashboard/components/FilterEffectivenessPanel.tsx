import { useEffect, useState } from "react";
import { Filter, TrendingUp, TrendingDown } from "lucide-react";
import { api } from "../../../api/client";
import { scoreColor } from "../../../utils/display";
import type { FilterEffectiveness } from "../../../types";

export default function FilterEffectivenessPanel() {
  const [data, setData] = useState<FilterEffectiveness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    api.filterEffectiveness("formal", { signal: controller.signal })
      .then(setData)
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  if (loading) return <section className="cockpit-panel"><span className="muted">过滤效果加载中…</span></section>;
  if (error || !data) return null;

  const rules = Object.entries(data.filtered_by_rule)
    .sort(([, a], [, b]) => b.count - a.count);

  const ruleLabels: Record<string, string> = {
    pos20_low: "pos20<50",
    avg_cost_high: "avg_cost>200",
    regime_ma5: "结构性强势×MA5",
  };

  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <Filter size={15} />
        <strong>三规则过滤效果</strong>
        <span className="muted" style={{ marginLeft: "auto", fontSize: "var(--text-xs)" }}>
          过滤率 {data.filter_rate_pct}%
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", marginTop: "var(--space-3)" }}>
        {/* 保留交易指标 */}
        <div>
          <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)", marginBottom: "var(--space-2)" }}>
            保留交易（{data.kept.count} 笔）
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)" }}>
            <span style={{
              fontSize: "var(--text-2xl)",
              fontFamily: "var(--font-mono)",
              fontWeight: "var(--weight-bold)",
              color: scoreColor(data.kept.net_win_rate),
            }}>
              {data.kept.net_win_rate}%
            </span>
            <span style={{ fontSize: "var(--text-xs)", color: "var(--muted)" }}>净胜率</span>
          </div>
          <div style={{ display: "flex", gap: "var(--space-4)", marginTop: "var(--space-1)" }}>
            <span style={{ fontSize: "var(--text-sm)", fontFamily: "var(--font-mono)" }}>
              <TrendingUp size={12} style={{ verticalAlign: "middle" }} />
              +{data.kept.net_avg_return}%
            </span>
            <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)" }}>
              PF {data.kept.net_profit_factor}
            </span>
          </div>
        </div>

        {/* 各规则过滤 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {rules.map(([key, info]) => (
            <div key={key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: "var(--text-xs)", color: "var(--ink-secondary)" }}>
                {ruleLabels[key] || key}
              </span>
              <span style={{
                fontSize: "var(--text-xs)",
                fontFamily: "var(--font-mono)",
                color: info.win_rate < 50 ? "var(--positive)" : "var(--muted)",
              }}>
                {info.count} 笔 · WR {info.win_rate}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
