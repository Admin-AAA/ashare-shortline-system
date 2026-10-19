import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type MetricsSampleScope } from "../api/client";
import { strategyShortName } from "./strategyRules";

/** 策略表现 · LIVE 前瞻 / 历史重建 分开统计（/api/metrics/by-strategy?sample_scope=），两类证据不混算。 */
export function ScopedStrategyStats() {
  const [scope, setScope] = useState<MetricsSampleScope>("live_forward");
  const q = useQuery({ queryKey: ["by-strategy", scope], queryFn: () => api.metricsByStrategy(undefined, scope) });
  const rows = [...(q.data ?? [])].sort((a, b) => b.sample_count - a.sample_count);
  const pct = (v: number | null | undefined) => (v == null ? "—" : `${(Math.abs(v) <= 1 ? v * 100 : v).toFixed(1)}%`);
  return (
    <section className="card" style={{ padding: 16, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
        <b>策略表现</b>
        <span className="muted small">{scope === "live_forward" ? "仅 LIVE 前瞻实盘推荐" : "历史重建回放样本，仅作研究参考，数字带“回放”标"}</span>
        <div className="seg" style={{ marginLeft: "auto" }}>
          <button className={scope === "live_forward" ? "on" : ""} onClick={() => setScope("live_forward")}>LIVE 前瞻</button>
          <button className={scope === "historical_replay" ? "on" : ""} onClick={() => setScope("historical_replay")}>历史重建</button>
        </div>
      </div>
      {q.isLoading ? <div className="muted small">加载中…</div> : q.error ? <div className="muted small">加载失败：{(q.error as Error).message}</div> : rows.length === 0 ? <div className="muted small">该口径暂无样本</div> : (
        <table className="tbl">
          <thead><tr><th>策略</th><th>模式</th><th className="n">样本</th><th className="n">已触发</th><th className="n">胜率</th><th className="n">平均收益</th><th className="n">平均规则分</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.strategy_id}>
              <td>{r.strategy_name || strategyShortName(r.strategy_id)} {scope === "historical_replay" && <span className="tag tag--gray">回放</span>}</td>
              <td className="muted">{r.current_mode || "—"}</td>
              <td className="n num">{r.sample_count}</td>
              <td className="n num">{r.triggered_count ?? "—"}</td>
              <td className="n num">{pct(r.hit_rate)}</td>
              <td className={`n num ${r.avg_return > 0 ? "up" : r.avg_return < 0 ? "down" : ""}`}>{pct(r.avg_return)}</td>
              <td className="n num">{r.avg_score?.toFixed(1) ?? "—"}</td>
            </tr>))}</tbody>
        </table>)}
    </section>
  );
}
