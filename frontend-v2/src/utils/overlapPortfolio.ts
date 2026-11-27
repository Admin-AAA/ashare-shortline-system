/** 中线诊断页「组合视图」的纯函数（便于单测）：A/B 逐月对齐、交易日 X 轴刻度、曲线末端标签避让。
 * 数据口径在后端 backend/app/services/overlap_portfolio.py，这里不重算收益。 */

export type MonthRow = { month: string; days: number; net_ret: number | null; bench_ret: number | null; excess_ret: number | null; max_drawdown: number | null };
export type MonthPair = { month: string; days: number; a: MonthRow; b: MonthRow | null; diff: number | null };

/** 按月份把 A、B 对齐（后端都按月份新→旧给）。diff = A 净收益 − B 净收益（百分点，按页面显示的两位小数算）。
 * aWins 只数 diff > 0 的月份，持平不算；compared 是两边都有数的月份数。 */
export function pairMonths(a: MonthRow[], b: MonthRow[]): { rows: MonthPair[]; aWins: number; compared: number } {
  const byMonth = new Map(b.map((m) => [m.month, m]));
  let aWins = 0;
  let compared = 0;
  const rows = a.map((m) => {
    const other = byMonth.get(m.month) ?? null;
    const diff = other && m.net_ret != null && other.net_ret != null ? Number((m.net_ret - other.net_ret).toFixed(2)) : null;
    if (diff != null) {
      compared += 1;
      if (diff > 0) aWins += 1;
    }
    return { month: m.month, days: m.days, a: m, b: other, diff };
  });
  return { rows, aWins, compared };
}

/** 交易日序号轴上等距取 count 个刻度下标，首尾必含、去重、升序。 */
export function indexTicks(n: number, count = 5): number[] {
  if (n <= 0) return [];
  if (n === 1 || count < 2) return [0];
  const out = new Set<number>();
  for (let k = 0; k < count; k += 1) out.add(Math.round((k * (n - 1)) / (count - 1)));
  return [...out].sort((x, y) => x - y);
}

/** 曲线末端标签避让（SVG y 向下）：按 y 排序后逐个下推，相邻至少隔 gap；越过 hi 时只把挤在底部的几个往上收，
 * 最后不低于 lo。返回与输入同序的 y。空间不够放下全部标签时以不重叠优先。 */
export function spreadLabels(ys: number[], gap: number, lo: number, hi: number): number[] {
  const order = ys.map((y, i) => ({ y: Math.min(Math.max(y, lo), hi), i })).sort((p, q) => p.y - q.y);
  const placed = order.map((p) => p.y);
  for (let k = 1; k < placed.length; k += 1) placed[k] = Math.max(placed[k], placed[k - 1] + gap);
  const last = placed.length - 1;
  if (last >= 0 && placed[last] > hi) {
    placed[last] = hi;
    for (let k = last - 1; k >= 0; k -= 1) placed[k] = Math.min(placed[k], placed[k + 1] - gap);
  }
  if (placed.length && placed[0] < lo) {
    placed[0] = lo;
    for (let k = 1; k < placed.length; k += 1) placed[k] = Math.max(placed[k], placed[k - 1] + gap);
  }
  const out = new Array<number>(ys.length);
  order.forEach((p, k) => { out[p.i] = placed[k]; });
  return out;
}
