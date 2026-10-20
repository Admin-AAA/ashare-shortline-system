import { isOutcomeSettled } from "./display.ts";

/** 复盘页收益曲线（纯函数，便于单测）。
 * 口径：同一平仓日「已买入且已结算」的票等权平均为当日收益，按平仓日单利累加（单位：百分点）。
 * - 不用逐笔累加做主曲线：当天推 10 只就占 10 份权重，曲线跟「推得多」相关而不是「选得准」。逐笔累加只作对照数字。
 * - 不用逐日复利：持仓多为数天且互相重叠，逐日复利等于假设每天满仓，收益和回撤都被放大数倍。
 * 取消买入 / 未触发 / 过期 / 持有中（含 provisional 临时结果）不入曲线。 */
export type CurveItem = {
  execution_state?: { cancelled_before_entry?: boolean | null; latest_decision_code?: string | null } | null;
  outcome?: { entry_triggered?: boolean | null; outcome_label?: string | null; exit_date?: string | null; executed_return?: number | null; exit_reason?: string | null } | null;
};
export type CurvePoint = { date: string; n: number; dayRet: number; cum: number };
export type Classified = { kind: "closed"; date: string; ret: number } | { kind: "holding" | "excluded" };
export type EquityCurve = { points: CurvePoint[]; trades: number; sumPct: number; maxDrawdown: number; holding: number; excluded: number };

/** 与复盘页 statusOf 同序：先判取消/过期，再看是否成交、是否已结算 */
export function classify(it: CurveItem): Classified {
  const ex = it.execution_state;
  const o = it.outcome;
  if (ex?.cancelled_before_entry || ex?.latest_decision_code === "cancel" || ex?.latest_decision_code === "expired" || o?.exit_reason === "execution_cancelled_before_entry") return { kind: "excluded" };
  if (!o?.entry_triggered) return { kind: "excluded" };
  // provisional 也带 exit_date（=标记日），不能当平仓：只认已结算标签
  if (isOutcomeSettled(o.outcome_label) && o.exit_date && Number.isFinite(o.executed_return)) return { kind: "closed", date: o.exit_date, ret: o.executed_return as number };
  return { kind: "holding" };
}

export function buildEquityCurve<T = CurveItem>(items: T[], cls: (it: T) => Classified = classify as unknown as (it: T) => Classified): EquityCurve {
  const byDay = new Map<string, number[]>();
  let holding = 0;
  let excluded = 0;
  for (const it of items) {
    const c = cls(it);
    if (c.kind === "closed") byDay.set(c.date, [...(byDay.get(c.date) ?? []), c.ret]);
    else if (c.kind === "holding") holding += 1;
    else excluded += 1;
  }
  let cum = 0;
  let peak = 0;
  let maxDrawdown = 0;
  let sumPct = 0;
  let trades = 0;
  const points: CurvePoint[] = [...byDay.keys()].sort().map((date) => {
    const rets = byDay.get(date)!;
    const daySum = rets.reduce((a, r) => a + r, 0);
    const dayRet = daySum / rets.length;
    cum += dayRet;
    peak = Math.max(peak, cum);
    maxDrawdown = Math.min(maxDrawdown, cum - peak);
    sumPct += daySum;
    trades += rets.length;
    return { date, n: rets.length, dayRet, cum };
  });
  return { points, trades, sumPct, maxDrawdown, holding, excluded };
}

/** 中线 / 长线（/api/horizons/{h}/picks）：closed 且有收益与卖出日才入曲线；pending（还没买）与 skipped（开盘涨停取消）不入 */
export type HorizonCurvePick = { status: string; ret?: number | null; exit_date?: string | null; cancel_code?: string | null };
export function classifyHorizon(p: HorizonCurvePick): Classified {
  if (p.status === "skipped" || p.cancel_code === "limit_up_open" || p.status === "pending") return { kind: "excluded" };
  if (p.status === "closed" && p.exit_date && Number.isFinite(p.ret)) return { kind: "closed", date: p.exit_date, ret: p.ret as number };
  return { kind: "holding" };
}
