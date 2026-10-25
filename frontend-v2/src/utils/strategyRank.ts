/**
 * 策略排序（综合分 / 收益 / 胜率 / 回撤 / 近期，升降序）—— 只在同一周期（短 / 中 / 长）内比较。
 * 综合分由后端算（backend/app/services/strategy_rank.py），这里只负责排序切换：
 *  - 沉底策略（rank_flags 非空或 rank_score 为空：样本不足 / 缺指标 / 数据过期）任何排序下都在最后，保持接口返回的原始顺序；
 *  - 数值排序按“值大的在前”为降序；回撤是负数，所以降序 = 回撤最小的在前；
 *  - 排序键缺值的放在可排序策略的最后；并列保持接口返回的顺序（稳定）。
 */
export type SortKey = "composite" | "return" | "winrate" | "drawdown" | "recent";
export type SortDir = "desc" | "asc";
export interface SortState { key: SortKey; dir: SortDir }

export const DEFAULT_SORT: SortState = { key: "composite", dir: "desc" };

export const SORT_KEYS: { key: SortKey; label: string; hint: string }[] = [
  { key: "composite", label: "综合", hint: "综合分（均值 + 收益/回撤 + 盈亏比 + N3 均值 + 阶段稳定性，按指标实际数值缩放、样本少向中性收缩）" },
  { key: "return", label: "收益", hint: "中长线：近12月组合收益（折算到20仓位口径）；短线：近12月平均每笔收益" },
  { key: "winrate", label: "胜率", hint: "近12月胜率" },
  { key: "drawdown", label: "回撤", hint: "近12月最大回撤（降序 = 回撤小的在前）" },
  { key: "recent", label: "近期", hint: "中长线：近12月平均每笔收益；短线：近期窗口（2026-05 起）平均每笔收益" },
];

export interface RankFields {
  rank_score?: number | null;
  rank?: number | null;
  rank_flags?: string[] | null;
  rank_flag_labels?: string[] | null;
}

/** 单个策略用于排序的数值；缺值给 null。 */
export interface SortMetrics { return?: number | null; winrate?: number | null; drawdown?: number | null; recent?: number | null }

export function isSunk(r: RankFields | undefined | null): boolean {
  if (!r) return true;
  return (r.rank_flags?.length ?? 0) > 0 || r.rank_score == null;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function sortStrategies<T>(
  items: readonly T[],
  rankOf: (t: T) => RankFields | undefined | null,
  metricsOf: (t: T) => SortMetrics,
  sort: SortState = DEFAULT_SORT,
): T[] {
  const idx = new Map<T, number>();
  items.forEach((t, i) => idx.set(t, i));
  const live: T[] = [];
  const sunk: T[] = [];
  for (const t of items) (isSunk(rankOf(t)) ? sunk : live).push(t);
  const sign = sort.dir === "desc" ? -1 : 1;
  const val = (t: T): number | null => {
    if (sort.key === "composite") return rankOf(t)?.rank_score ?? null;
    const v = metricsOf(t)[sort.key];
    return finite(v) ? v : null;
  };
  live.sort((a, b) => {
    const va = val(a), vb = val(b);
    if (va == null && vb == null) return idx.get(a)! - idx.get(b)!;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va !== vb) return sign * (va - vb);
    return idx.get(a)! - idx.get(b)!;
  });
  return [...live, ...sunk];
}

/** 中长线策略（/api/horizons/{h}/strategies）的排序指标：收益/回撤用折算到 20 仓位后的值。 */
export function horizonMetrics(s: {
  metrics?: { recent_12m?: { win?: number; avg?: number; tot?: number; mdd?: number } };
  rank_components?: { tot_20?: number | null; mdd_20?: number | null } | null;
}): SortMetrics {
  const m = s.metrics?.recent_12m;
  return {
    return: s.rank_components?.tot_20 ?? m?.tot ?? null,
    winrate: m?.win ?? null,
    drawdown: s.rank_components?.mdd_20 ?? m?.mdd ?? null,
    recent: m?.avg ?? null,
  };
}

/** 短线（/api/strategies/standalone-metrics，口径 A）的排序指标。 */
export function standaloneMetrics(s: {
  last_12m?: { win_rate?: number; avg_return_pct?: number; max_drawdown_pct?: number };
  recent?: { avg_return_pct?: number };
}): SortMetrics {
  return {
    return: s.last_12m?.avg_return_pct ?? null,
    winrate: s.last_12m?.win_rate ?? null,
    drawdown: s.last_12m?.max_drawdown_pct ?? null,
    recent: s.recent?.avg_return_pct ?? null,
  };
}

export function formatRankScore(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(1);
}
