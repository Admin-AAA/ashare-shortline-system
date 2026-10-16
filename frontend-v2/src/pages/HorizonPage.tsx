import { useState } from "react";
import { api, type HorizonBatch, type HorizonMetrics, type HorizonStrategiesResponse, type HorizonPicksResponse } from "../api/client";
import { Panel } from "../components/Panel";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";
import { useApi } from "../app/useApi";

/** 中线 / 长线页：策略画像（近12个月 + 历史分段回测）、最新选股、持仓与复盘。
 * 只展示通过回测的现役策略；淘汰的思路在底部折叠列出原因。A 股配色：涨红跌绿。 */
export function HorizonPage({ horizon }: { horizon: "mid" | "long" }) {
  const strategies = useApi<HorizonStrategiesResponse>(() => api.horizonStrategies(horizon), [horizon]);
  const picks = useApi<HorizonPicksResponse | null>(
    () => (horizon === "mid" ? api.horizonPicks("mid", undefined, 40) : Promise.resolve(null)),
    [horizon],
  );
  const title = horizon === "mid" ? "中线策略（持有约 2–8 周）" : "长线策略（持有数月）";

  if (strategies.loading) return <PageSkeleton title="加载中" />;
  if (strategies.error) return <ErrorPage title="加载失败" message={strategies.error} onRetry={strategies.refetch} />;
  const list = strategies.data?.strategies ?? [];

  return (
    <div style={{ display: "grid", gap: "var(--space-4)" }}>
      <Panel kicker={horizon === "mid" ? "中线" : "长线"} title={title}>
        {list.length === 0 ? (
          <EmptyState title="暂无通过回测的策略" reason="候选思路最近12个月或 2022–2024 分段回测不达标，未上线。" />
        ) : (
          list.map((s) => (
            <div key={s.id} style={{ marginBottom: "var(--space-4)" }}>
              <h3 style={{ margin: "0 0 4px" }}>{s.name}</h3>
              <p style={{ margin: "0 0 8px", opacity: 0.8, fontSize: 13 }}>{s.summary}</p>
              <MetricsTable recent={s.metrics.recent_12m} phases={s.metrics.phases} />
              {s.metrics.note && <p style={{ fontSize: 12, opacity: 0.6, margin: "6px 0 0" }}>{s.metrics.note}</p>}
            </div>
          ))
        )}
      </Panel>

      {horizon === "mid" && (
        <Panel kicker="选股与复盘" title="每周选股 · 持仓 · 已平仓收益">
          {picks.loading ? <PageSkeleton title="加载中" /> : picks.error ? <ErrorPage title="加载失败" message={picks.error} onRetry={picks.refetch} /> :
            (picks.data?.batches ?? []).length === 0 ? <EmptyState title="还没有选股记录" reason="周五盘后生成。" /> :
            <Batches batches={picks.data!.batches} />}
        </Panel>
      )}

      {(strategies.data?.rejected ?? []).length > 0 && (
        <Panel kicker="已淘汰" title="回测未通过的思路">
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {strategies.data!.rejected!.map((r) => <li key={r.name}><b>{r.name}</b>：{r.reason}</li>)}
          </ul>
        </Panel>
      )}
    </div>
  );
}

const pct = (v?: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const color = (v?: number | null) => (v == null ? undefined : v > 0 ? "var(--up, #d14343)" : v < 0 ? "var(--down, #2f9e5b)" : undefined);
const cell = { padding: "4px 10px", textAlign: "right" as const, whiteSpace: "nowrap" as const };

function MetricsTable({ recent, phases }: { recent?: HorizonMetrics; phases?: Record<string, HorizonMetrics> }) {
  const rows: [string, HorizonMetrics | undefined][] = [["最近12个月", recent], ...Object.entries(phases ?? {}).filter(([k]) => !k.startsWith("R12"))];
  return (
    <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
      <thead><tr>{["区间", "笔数", "胜率", "平均收益/笔", "PF", "组合收益", "最大回撤"].map((h) => <th key={h} style={cell}>{h}</th>)}</tr></thead>
      <tbody>
        {rows.map(([k, m]) => (
          <tr key={k} style={k === "最近12个月" ? { fontWeight: 600 } : undefined}>
            <td style={{ ...cell, textAlign: "left" }}>{k}</td>
            <td style={cell}>{m?.n ?? "—"}</td>
            <td style={cell}>{m?.win != null ? `${m.win}%` : "—"}</td>
            <td style={{ ...cell, color: color(m?.avg) }}>{pct(m?.avg)}</td>
            <td style={cell}>{m?.pf ?? "—"}</td>
            <td style={{ ...cell, color: color(m?.tot) }}>{pct(m?.tot)}</td>
            <td style={cell}>{m?.mdd != null ? `${m.mdd}%` : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const STATUS: Record<string, string> = { pending: "待买入", holding: "持有中", closed: "已平仓", skipped: "涨停买不进" };

function Batches({ batches }: { batches: HorizonBatch[] }) {
  const [open, setOpen] = useState<string | null>(batches[0] ? batches[0].signal_date + batches[0].strategy_id : null);
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {batches.map((b) => {
        const key = b.signal_date + b.strategy_id;
        return (
          <div key={key} style={{ borderBottom: "1px solid rgba(127,127,127,0.15)", paddingBottom: 6 }}>
            <button onClick={() => setOpen(open === key ? null : key)}
              style={{ all: "unset", cursor: "pointer", display: "flex", gap: 16, fontSize: 13, width: "100%" }}>
              <span style={{ width: 96 }}>{b.signal_date}</span>
              <span style={{ width: 120 }}>{b.strategy_name}</span>
              <span style={{ width: 64 }}>{STATUS[b.status] ?? b.status}</span>
              <span style={{ color: color(b.avg_ret) }}>平均 {pct(b.avg_ret)}</span>
              <span style={{ marginLeft: "auto", opacity: 0.6 }}>{b.picks.length} 只</span>
            </button>
            {open === key && (
              <table style={{ borderCollapse: "collapse", fontSize: 12, marginTop: 6 }}>
                <thead><tr>{["代码", "名称", "总市值(亿)", "状态", "买入日", "卖出/估值日", "收益"].map((h) => <th key={h} style={cell}>{h}</th>)}</tr></thead>
                <tbody>
                  {b.picks.map((p) => (
                    <tr key={p.code}>
                      <td style={{ ...cell, textAlign: "left" }}>{p.code}</td>
                      <td style={{ ...cell, textAlign: "left" }}>{p.name}</td>
                      <td style={cell}>{(p.total_mv / 1e4).toFixed(1)}</td>
                      <td style={cell}>{STATUS[p.status] ?? p.status}</td>
                      <td style={cell}>{p.entry_date ?? "—"}</td>
                      <td style={cell}>{p.exit_date ?? p.mark_date ?? "—"}</td>
                      <td style={{ ...cell, color: color(p.ret) }}>{pct(p.ret)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
    </div>
  );
}
