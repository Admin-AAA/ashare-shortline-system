/** 短线策略元数据 + 当日暂停规则（领域负责人口径）。
 * 后端目前没有直接返回「今日暂停」字段，这里按规则基于 /api/home/bootstrap 的 market_snapshot 推算，
 * UI 上标注「按规则推算」。*/
export type StrategyMeta = { id: string; short: string; status: "运行中" | "观察" };
export const SHORT_STRATEGIES: StrategyMeta[] = [
  { id: "chao-gu-yang-jia-skill", short: "炒股养家", status: "运行中" },
  { id: "sector-relative-strength-leader", short: "板块龙头", status: "运行中" },
  { id: "oversold-capital-reversal", short: "超跌反转", status: "运行中" },
  { id: "chen-xiaoqun-skill", short: "陈小群", status: "观察" },
  { id: "high-range-breakout-momentum", short: "高位区间突破", status: "观察" },
];
export const strategyMeta = (id?: string | null) => SHORT_STRATEGIES.find((s) => s.id === id);
export const strategyShortName = (id?: string | null, fallback?: string | null) =>
  strategyMeta(id)?.short ?? fallback ?? id ?? "—";

export type MarketState = { regime: string; upRatio: number | null; limitUp: number; limitDown: number };

export function pauseReason(id: string, m: MarketState | null): string | null {
  if (!m) return null;
  const choppy = m.regime.includes("震荡");
  const hot = m.regime.includes("过热");
  if (id === "chao-gu-yang-jia-skill") {
    if (choppy || hot) return `市场${choppy ? "震荡" : "过热"}`;
    if (m.upRatio != null && m.upRatio < 0.3) return "上涨占比<30%";
  }
  if (id === "chen-xiaoqun-skill" && choppy) return "市场震荡";
  if (id === "sector-relative-strength-leader" && m.limitUp - m.limitDown < 40) return "涨停−跌停<40";
  return null;
}
