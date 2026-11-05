import type { CSSProperties } from "react";
import { AlertTriangle } from "lucide-react";
import { api, type DataReadinessResponse, type StrategyDataReadiness } from "../api/client";
import { useApi } from "../app/useApi";

/** 出票前数据就绪闸门的提示（GET /api/strategies/data-readiness，同一页面多张卡片共用一次请求）。
 * 只在缺数据时显示，文字直接用后端给的中文原因。两种情况：
 * - recorded：最近一个出票日被闸门拦下，那天没出票；
 * - live：现在依赖的数据仍不是最新（下次出票会被拦）。 */
export function useDataReadiness() {
  return useApi<DataReadinessResponse>(() => api.strategyDataReadiness(), []);
}

const md = (d?: string | null) => (d && d.length >= 10 ? d.slice(5) : d ?? "");

const box: CSSProperties = {
  display: "flex", gap: 6, alignItems: "flex-start", marginTop: 6, padding: "6px 8px", borderRadius: 6,
  color: "var(--warning)", background: "color-mix(in srgb, var(--warning) 12%, transparent)",
  fontSize: "var(--text-xs)", lineHeight: 1.5,
};

export function noteLines(r: StrategyDataReadiness | undefined): string[] {
  if (!r) return [];
  const out: string[] = [];
  const rec = r.recorded;
  if (rec && rec.ready === false) {
    const fixed = r.live.ready ? "（现在数据已补齐，等补跑出票）" : "";
    out.push(`${md(rec.trade_date)} 没出票，${rec.reason ?? "缺数据"}${fixed}`);
  }
  if (!r.live.ready && !(rec && rec.ready === false && rec.reason === r.live.reason)) {
    out.push(`${r.live.reason}（数据不补上，下次不出票）`);
  }
  return out;
}

/** 单个策略卡片里的小提示 */
export function DataReadinessNote({ id }: { id: string }) {
  const q = useDataReadiness();
  const lines = noteLines(q.data?.strategies?.[id]);
  if (!lines.length) return null;
  return (
    <div style={box} title={q.data?.rule}>
      <AlertTriangle size={13} style={{ flex: "none", marginTop: 2 }} />
      <div>{lines.map((l) => <div key={l}>{l}</div>)}</div>
    </div>
  );
}

/** 今日出票页顶部：本周期里缺数据的策略汇总 */
export function DataReadinessBanner({ horizon }: { horizon: "short" | "mid" | "long" }) {
  const q = useDataReadiness();
  const rows = Object.values(q.data?.strategies ?? {}).filter((r) => r.horizon === horizon)
    .map((r) => ({ r, lines: noteLines(r) })).filter((x) => x.lines.length);
  if (!rows.length) return null;
  return (
    <section className="panel" style={{ padding: "10px 14px", marginBottom: 12 }}>
      <div style={{ ...box, marginTop: 0, fontSize: "var(--text-sm)" }} title={q.data?.rule}>
        <AlertTriangle size={15} style={{ flex: "none", marginTop: 2 }} />
        <div>
          <b>{rows.length} 个策略依赖的数据没更新到最新（缺数据的策略当天不出票，其它照常）</b>
          {rows.map(({ r, lines }) => <div key={r.strategy_id}>{r.name}：{lines.join("；")}</div>)}
        </div>
      </div>
    </section>
  );
}
