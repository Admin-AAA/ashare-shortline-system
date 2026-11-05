/** 画像条第三格文案。窗口起止由后端 portrait_realized_12m 给出，这里不推算天数。 */
export type RealizedWindow = { start?: string | null; end?: string | null; basis?: string | null };

export function realizedStripCopy(
  kind: "前瞻" | "回放",
  window?: RealizedWindow | null,
  lowNote = "",
): { label: string; tip: string } {
  const span = window?.start ? `信号日 ${window.start} 至 ${window.end || "今天"}` : "按信号日";
  if (kind === "回放") {
    return {
      label: "回放已平仓·近12个月",
      tip:
        `回放已平仓，只统计近12个月（${span}）。` +
        "来自已落盘的历史回放批次，不是实盘前瞻（该窗口内真实前瞻已平仓 0 笔）。" +
        "与研究画像「近12个月」同为约一年、按信号日截取，但样本来自回放重建，数字不必相同。" +
        lowNote,
    };
  }
  const windowNote = window?.start ? `，近12个月（${span}）` : "";
  return {
    label: window?.start ? "前瞻已结算·近12个月" : "前瞻已结算",
    tip: `前瞻已结算（仅实盘）${windowNote}。${lowNote}`,
  };
}

/** /api/metrics/by-strategy 一行里本函数用到的字段（短线）。 */
export type SoldStatsSource = {
  sold_count?: number;
  sold_avg_net_return?: number | null;
  sold_median_net_return?: number | null;
  triggered_count?: number;
  avg_return?: number;
};

/** by-strategy 一行 → 「已成交并卖出」统计（扣成本，不含未成交、不含未卖出），与中线「已平仓」同口径。
 * 新后端给 sold_*；旧后端没有时退回 avg_return（同样只算已成交）+ triggered_count，中位数缺失显示「—」。
 * 不用 average_net_return / trade_count：那是按推荐数平均，未成交记 0 收益，会把数字摊薄。 */
export function soldStats(r?: SoldStatsSource | null): { n: number | null; avg: number | null; median: number | null } | null {
  if (!r) return null;
  if (r.sold_count != null) return { n: r.sold_count, avg: r.sold_avg_net_return ?? null, median: r.sold_median_net_return ?? null };
  return { n: r.triggered_count ?? null, avg: r.triggered_count ? (r.avg_return ?? null) : null, median: null };
}
