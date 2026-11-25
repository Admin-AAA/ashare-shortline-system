import { isOutcomeSettled } from "./display.ts";

/** 复盘页收益曲线（纯函数，便于单测）。
 * 口径：同一平仓日「已买入且已结算」的票等权平均为当日收益，按平仓日单利累加（单位：百分点）。
 * - 不用逐笔累加做主曲线：当天推 10 只就占 10 份权重，曲线跟「推得多」相关而不是「选得准」。
 * - 不用逐日复利：持仓多为数天且互相重叠，逐日复利等于假设每天满仓，收益和回撤都被放大数倍。
 * - 已确认买入但还没结算的仓位，按「入场价 → 最新价」的浮动收益在标记日入账（open 点），
 *   所以曲线不必等平仓才有反应；这部分单独标 float，画成虚线，不与已实现收益混淆。
 * 取消买入 / 明确未成交 / 取不到价格的持有中仓位不入曲线。 */
export type CurveItem = {
  execution_state?: {
    cancelled_before_entry?: boolean | null;
    latest_decision_code?: string | null;
    ever_buy_confirmed?: boolean | null;
    latest_price?: number | null;
    latest_data_as_of?: string | null;
    first_buy_confirmed_price?: number | null;
    condition_summary?: { observed?: { open?: number | null; close?: number | null } | null } | null;
  } | null;
  outcome?: {
    entry_triggered?: boolean | null; entry_price?: number | null;
    outcome_label?: string | null; exit_date?: string | null; executed_return?: number | null; exit_reason?: string | null;
  } | null;
};
export type CurvePoint = { date: string; n: number; dayRet: number; float: number; open: number; cum: number };
export type Classified =
  | { kind: "closed"; date: string; ret: number }
  | { kind: "open"; date: string; ret: number }
  | { kind: "holding" | "excluded" };
export type EquityCurve = { points: CurvePoint[]; trades: number; maxDrawdown: number; holding: number; openCount: number; excluded: number };

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const dayOf = (v?: string | null): string | null => {
  const s = String(v ?? "").slice(0, 10);
  return DAY.test(s) ? s : null;
};

/** 持仓浮盈：已确认买入、但还没结算的仓位，用入场价与最新价算浮动收益（%）。
 * 入场价优先取 outcome.entry_price（已落库），否则按回测口径取入场日开盘价，
 * 最后退回首次确认买入价。日期或价格缺一不可，取不到就返回 null——宁可少画，不瞎画。 */
export function openMark(it: CurveItem): { date: string; ret: number } | null {
  const ex = it.execution_state;
  if (!ex?.ever_buy_confirmed && ex?.latest_decision_code !== "buy") return null;
  const date = dayOf(ex?.latest_data_as_of);
  const mark = ex?.latest_price;
  const entry = it.outcome?.entry_price ?? ex?.condition_summary?.observed?.open ?? ex?.first_buy_confirmed_price;
  if (!date || !Number.isFinite(mark) || !Number.isFinite(entry) || (entry as number) <= 0) return null;
  return { date, ret: ((mark as number) / (entry as number) - 1) * 100 };
}

/** 判定顺序：已结算 > 取消/过期 > 已确认买入的浮动 > 未成交。
 * 「已结算」必须排在最前：入场窗口过期后 latest_decision_code 会变成 cancel/expired，
 * 但它盖不掉已经止损/移动止盈平掉的真实成交，先判取消会把这类平仓整批丢掉。 */
export function classify(it: CurveItem): Classified {
  const ex = it.execution_state;
  const o = it.outcome;
  // provisional 也带 exit_date（=标记日），不能当平仓：只认已结算标签
  if (o?.entry_triggered && isOutcomeSettled(o.outcome_label) && o.exit_date && Number.isFinite(o.executed_return)) {
    return { kind: "closed", date: o.exit_date, ret: o.executed_return as number };
  }
  if (ex?.cancelled_before_entry || ex?.latest_decision_code === "cancel" || ex?.latest_decision_code === "expired" || o?.exit_reason === "execution_cancelled_before_entry") return { kind: "excluded" };
  if (o?.entry_triggered === false) return { kind: "excluded" };
  const mark = openMark(it);
  if (mark) return { kind: "open", ...mark };
  if (o?.entry_triggered) return { kind: "holding" };
  return { kind: "excluded" };
}

export function buildEquityCurve<T = CurveItem>(items: T[], cls: (it: T) => Classified = classify as unknown as (it: T) => Classified): EquityCurve {
  // 同一天可能既有平仓又有浮动标记：分别聚合，浮动用 open/float 单独记账，不混进「当日平仓均值」
  const byDay = new Map<string, { rets: number[]; floats: number[] }>();
  const bucket = (date: string) => {
    let b = byDay.get(date);
    if (!b) { b = { rets: [], floats: [] }; byDay.set(date, b); }
    return b;
  };
  let holding = 0;
  let openCount = 0;
  let excluded = 0;
  for (const it of items) {
    const c = cls(it);
    if (c.kind === "closed") bucket(c.date).rets.push(c.ret);
    else if (c.kind === "open") { bucket(c.date).floats.push(c.ret); openCount += 1; }
    else if (c.kind === "holding") holding += 1;
    else excluded += 1;
  }
  let cum = 0;
  let peak = 0;
  let maxDrawdown = 0;
  let trades = 0;
  const points: CurvePoint[] = [...byDay.keys()].sort().map((date) => {
    const { rets, floats } = byDay.get(date)!;
    const dayRet = rets.length ? rets.reduce((a, r) => a + r, 0) / rets.length : 0;
    const float = floats.length ? floats.reduce((a, r) => a + r, 0) / floats.length : 0;
    cum += dayRet + float;
    peak = Math.max(peak, cum);
    maxDrawdown = Math.min(maxDrawdown, cum - peak);
    trades += rets.length;
    return { date, n: rets.length, dayRet, float, open: floats.length, cum };
  });
  return { points, trades, maxDrawdown, holding, openCount, excluded };
}

/** 把后端按日期返回的沪深300点贴回策略曲线。按日期匹配，不按数组下标；
 * 后端没给的日期留空，调用方跳过基准点，策略点一个都不删。 */
export function alignBenchmarkPoints<T extends { date: string }>(dates: string[], points: T[]): Array<T | null> {
  const byDate = new Map<string, T>();
  for (const point of points) {
    if (!byDate.has(point.date)) byDate.set(point.date, point);
  }
  return dates.map((date) => byDate.get(date) ?? null);
}

/** 折线分段。y 为 null 的点断开，不把两侧连成一条假斜线。 */
export function lineSegments(coords: Array<{ x: number; y: number | null }>): string[] {
  const segments: string[] = [];
  let commands: string[] = [];
  for (const point of coords) {
    if (point.y == null || !Number.isFinite(point.y)) {
      if (commands.length) segments.push(commands.join(""));
      commands = [];
      continue;
    }
    commands.push(`${commands.length ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`);
  }
  if (commands.length) segments.push(commands.join(""));
  return segments;
}

/** 真实日期轴：把平仓日按自然日线性映射到 [0,1]。
 * 旧实现按「点序号」等距，间隔 1 天和间隔 9 天画出来一样宽，曲线形状会失真
 * （复盘样本里同一条曲线跨约一年，中间还有长假）。日期无法解析时退回等距。 */
export function dateAxis(dates: string[]): number[] {
  if (!dates.length) return [];
  const ts = dates.map((d) => Date.parse(`${String(d).slice(0, 10)}T00:00:00Z`));
  if (ts.some((t) => !Number.isFinite(t))) {
    return dates.map((_, i) => (dates.length > 1 ? i / (dates.length - 1) : 0.5));
  }
  const first = ts[0];
  const last = ts[ts.length - 1];
  if (last === first) return dates.map(() => 0.5);
  return ts.map((t) => (t - first) / (last - first));
}

/** 规整 Y 轴刻度：步长取 1/2/2.5/5/10 × 10^n，覆盖 [lo,hi]，并保证 0 基线在内。 */
export function niceTicks(lo: number, hi: number, target = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return [0];
  const raw = (hi - lo) / Math.max(2, target - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) {
    out.push(Math.abs(v) < step * 1e-6 ? 0 : Number(v.toFixed(6)));
  }
  // 顶部没被刻度覆盖时补一档，否则绘图区上沿会缺一条网格线
  if (out.length && out[out.length - 1] < hi - step * 1e-6) {
    out.push(Number((out[out.length - 1] + step).toFixed(6)));
  }
  if (lo <= 0 && hi >= 0 && !out.includes(0)) out.push(0);
  return out.sort((a, b) => a - b);
}

/** 悬浮定位：按真实 x 位置找最近的平仓日。日期轴不再等距，不能再按索引反算。 */
export function nearestIndex(positions: number[], ratio: number): number {
  if (!positions.length) return 0;
  let best = 0;
  let dist = Infinity;
  for (let i = 0; i < positions.length; i += 1) {
    const d = Math.abs(positions[i] - ratio);
    if (d < dist) { dist = d; best = i; }
  }
  return best;
}

/** 中线 / 长线（/api/horizons/{h}/picks）：closed 且有收益与卖出日入曲线；pending（还没买）与 skipped（开盘涨停取消）不入；
 * holding 且带 mark_date / ret 的按浮动入账（不必等 20 天持有期走完）。 */
export type HorizonCurvePick = { status: string; ret?: number | null; exit_date?: string | null; mark_date?: string | null; cancel_code?: string | null };
export function classifyHorizon(p: HorizonCurvePick): Classified {
  if (p.status === "skipped" || p.cancel_code === "limit_up_open" || p.status === "pending") return { kind: "excluded" };
  if (p.status === "closed" && p.exit_date && Number.isFinite(p.ret)) return { kind: "closed", date: p.exit_date, ret: p.ret as number };
  if (p.status === "holding" && p.mark_date && Number.isFinite(p.ret)) return { kind: "open", date: p.mark_date, ret: p.ret as number };
  return { kind: "holding" };
}
