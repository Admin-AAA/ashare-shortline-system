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
