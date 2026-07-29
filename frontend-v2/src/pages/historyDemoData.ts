/**
 * HistoryPage 重构 demo 的共享 mock 数据与工具。
 * 目的：在无需后端的情况下，也能完整展示两种新布局的视觉效果。
 */
import type {
  RecommendationHistoryResponse,
  RecommendationCard,
  RecommendationOutcome,
  RecommendationExecutionState,
  CancelObservation,
  ExecutionDecision,
} from "../types";

export type DemoHistoryItem = RecommendationCard & {
  outcome: RecommendationOutcome | null;
  execution_state: RecommendationExecutionState | null;
  cancel_observation: CancelObservation | null;
  /** 实时持仓/卖出决策（后端来自 /api/recommendations/execution-confirmations 的 holding_decision） */
  holding_decision?: ExecutionDecision | null;
};

const BUCKETS = [
  "daily-stock-analysis-skill",
  "chao-gu-yang-jia-skill",
  "chen-xiaoqun-skill",
  "chip-single-peak-breakout",
  "high-range-breakout-momentum",
  "small-cap-thematic-momentum",
  "trend-pullback-continuation",
  "weak-market-strong-stock-reversal",
  "sector-relative-strength-leader",
];

const SETUP_TYPES = [
  "首板放量分歧转一致",
  "趋势缩量回踩低吸",
  "主线龙头分歧低吸",
  "筹码单峰突破",
  "阶段新高放量突破",
  "弱市强势股确认回撤",
  "",
];

const NAMES = [
  "宁德时代", "贵州茅台", "比亚迪", "中芯国际", "东方财富", "隆基绿能",
  "中国平安", "招商银行", "五粮液", "美的集团", "迈瑞医疗", "恒瑞医药",
  "海康威视", "立讯精密", "顺丰控股", "伊利股份", "药明康德", "中信证券",
  "中国中免", "金山办公", "科大讯飞", "北方华创", "韦尔股份", "通策医疗",
];

function randInt(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randPick<T>(arr: T[]): T {
  return arr[randInt(0, arr.length - 1)];
}

function randBool(p = 0.5) {
  return Math.random() < p;
}

function pad2(n: number) {
  return n.toString().padStart(2, "0");
}

function makeCode() {
  const prefixes = ["600", "601", "603", "605", "000", "001", "002", "300", "688"];
  const prefix = randPick(prefixes);
  const suffix = randInt(0, 999999).toString().padStart(6 - prefix.length, "0");
  return `${prefix}${suffix}`;
}

function makeDate(offsetDays: number) {
  const d = new Date();
  d.setDate(d.getDate() - offsetDays);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function makeRecommendedAt(tradeDate: string) {
  const hour = randInt(9, 22);
  const minute = randInt(0, 59);
  return `${tradeDate}T${pad2(hour)}:${pad2(minute)}:00`;
}

function makeOutcome(label: "success" | "failed" | "missed" | "pending" | "provisional", recPrice: number): RecommendationOutcome | null {
  if (label === "missed" || label === "pending") {
    return {
      recommendation_id: "",
      outcome_label: label,
      exit_reason: label === "missed" ? "not_triggered" : null,
      executed_return: null,
      entry_price: null,
      stop_loss_price: null,
      take_profit_price: null,
      holding_days: null,
      settled_at: null,
    } as unknown as RecommendationOutcome;
  }
  const entry = recPrice * (1 + (Math.random() - 0.45) * 0.04);
  const isSuccess = label === "success";
  const ret = isSuccess
    ? randInt(25, 110) / 10
    : randInt(-95, -15) / 10;
  const exitReason = isSuccess
    ? randPick(["take_profit_hit", "trailing_stop_hit", "horizon_close"])
    : randPick(["stop_loss_hit", "stop_loss_gap", "trailing_stop_hit"]);
  return {
    recommendation_id: "",
    outcome_label: label,
    exit_reason: exitReason,
    executed_return: ret,
    entry_price: entry,
    stop_loss_price: entry * 0.94,
    take_profit_price: entry * 1.08,
    holding_days: randInt(1, 6),
    settled_at: makeDate(randInt(0, 5)),
  } as unknown as RecommendationOutcome;
}

function makeExecutionState(
  label: string,
): RecommendationExecutionState | null {
  if (["missed", "pending", "provisional"].includes(label)) return null;
  const code = randBool(0.6)
    ? "buy"
    : randBool(0.5)
      ? "wait"
      : "cancel";
  return {
    latest_decision_code: code,
    latest_decision_label:
      code === "buy" ? "买点成立" : code === "wait" ? "继续等待" : "已取消",
    latest_reason: code === "buy" ? "价格触及买点且量能配合" : "条件未完全满足",
    latest_checked_at: makeRecommendedAt(makeDate(randInt(0, 10))),
    latest_price: randInt(2000, 20000) / 100,
    ever_buy_confirmed: code === "buy" && randBool(0.7),
    first_buy_confirmed_at: code === "buy" ? makeRecommendedAt(makeDate(randInt(0, 5))) : null,
    first_buy_confirmed_price: code === "buy" ? randInt(2000, 20000) / 100 : null,
    cancelled_before_entry: code === "cancel",
    first_cancelled_at: code === "cancel" ? makeRecommendedAt(makeDate(randInt(0, 3))) : null,
    first_cancelled_price: code === "cancel" ? randInt(2000, 20000) / 100 : null,
    first_cancel_reason: code === "cancel" ? "高开超过阈值或触发失效条件" : null,
    condition_summary: {
      label: "收盘价 ≥ 理想买点",
      passed: code === "buy",
      expression: "close >= ideal_buy",
    },
  };
}

function makeHoldingDecision(
  label: "success" | "failed" | "missed" | "pending" | "provisional",
  recPrice: number,
): ExecutionDecision | null {
  // 未触发/待标注的票没有持仓，不需要卖出决策
  if (["missed", "pending"].includes(label)) return null;
  // 已结算的票在结算前也曾有实时卖出决策，这里演示一下；provisional 是重点场景
  const currentPrice = recPrice * (1 + (Math.random() - 0.5) * 0.08);
  const stopLoss = recPrice * 0.94;
  const takeProfit = recPrice * 1.08;
  if (currentPrice < stopLoss) {
    return {
      code: "exit",
      label: "退出持仓",
      reason: "当前价格已经跌破原推荐止损位",
    };
  }
  if (currentPrice > takeProfit) {
    return {
      code: "hold",
      label: "继续持有并执行保护位",
      reason: "已触发止盈区域，建议上移保护位锁定利润",
    };
  }
  const code = randPick(["hold", "hold", "reduce", "reduce", "observe"] as const);
  const labels: Record<string, string> = {
    hold: "继续持有并执行保护位",
    reduce: "收紧保护并观察",
    observe: "等待最新数据",
  };
  const reasons: Record<string, string> = {
    hold: "原策略逻辑仍成立，量价未失控",
    reduce: "盘面出现分歧，建议收紧止损保护本金",
    observe: "实时行情尚未刷新或处于集合竞价阶段",
  };
  return {
    code,
    label: labels[code],
    reason: reasons[code],
  };
}

function makeCancelObservation(): CancelObservation | null {
  if (randBool(0.75)) return null;
  const ref = randInt(2000, 20000) / 100;
  const point = (days: number): CancelObservation["horizons"]["1d"] => {
    const ret = (Math.random() - 0.45) * 8;
    return {
      trade_date: makeDate(days),
      close: ref * (1 + ret / 100),
      net_return_pct: ret - 0.07,
    };
  };
  return {
    status: "complete",
    reference_at: makeRecommendedAt(makeDate(randInt(0, 5))),
    reference_price: ref,
    cost_pct: 0.07,
    horizons: { "1d": point(1), "3d": point(3), "5d": point(5) },
  };
}

export function generateMockHistory(count = 60): RecommendationHistoryResponse {
  const items: DemoHistoryItem[] = [];
  for (let i = 0; i < count; i++) {
    const tradeDate = makeDate(randInt(0, 35));
    const recPrice = randInt(1000, 50000) / 100;
    const label = randPick([
      "success", "success", "failed", "missed", "pending", "provisional",
    ]) as "success" | "failed" | "missed" | "pending" | "provisional";
    const outcome = makeOutcome(label, recPrice);
    const executionState = makeExecutionState(label);
    const holdingDecision = makeHoldingDecision(label, recPrice);
    const item = {
      recommendation_id: `demo-rec-${i}`,
      run_id: `demo-run-${randInt(1, 8)}`,
      trade_date: tradeDate,
      recommended_at: makeRecommendedAt(tradeDate),
      code: makeCode(),
      name: randPick(NAMES),
      industry: randPick(["电力设备", "食品饮料", "半导体", "非银金融", "医药生物", "计算机", "汽车", "电子"]),
      recommend_price: recPrice,
      ideal_buy: recPrice * 0.985,
      secondary_buy: recPrice * 0.96,
      stop_loss: recPrice * 0.94,
      take_profit: recPrice * 1.08,
      final_score: randInt(280, 980) / 10,
      consensus_score: randInt(200, 900) / 10,
      bucket: randPick(BUCKETS),
      setup_type: randPick(SETUP_TYPES),
      entry_rule: "收盘价 ≥ 理想买点 且 量能 ≥ 前5日均量1.2倍",
      invalid_rule: "开盘跳空高开 > 5% 或 盘中跌破止损价",
      rationale: "量价配合良好，主线辨识度提升，短线存在套利空间。",
      risk_level: randPick(["low", "medium", "high", "unknown"]),
      risk_tags: randBool(0.4) ? ["高位分歧"] : [],
      info_tags: randBool(0.4) ? ["主线龙头"] : [],
      degraded_reason: null,
      feature_snapshot: {
        price: recPrice,
        ma5: recPrice * 0.98,
        ma10: recPrice * 0.96,
        ma20: recPrice * 0.94,
        ma60: recPrice * 0.90,
      },
      market_context: {
        snapshot_id: `snap-${i}`,
        trade_date: tradeDate,
        sample_count: randInt(4000, 5000),
        up_count: randInt(1500, 2500),
        down_count: randInt(1500, 2500),
        limit_up_count: randInt(30, 90),
        limit_down_count: randInt(5, 30),
        main_industries: ["电力设备", "半导体"],
        breadth: {},
      },
      skill_breakdown: [],
      llm_stance: randPick(["support", "neutral", "oppose"]),
      llm_delta: randInt(-30, 30) / 10,
      status: "formal",
      recent_repeat: randBool(0.1),
      watch_status: "none",
      watch_note: null,
      outcome,
      execution_state: executionState,
      cancel_observation: makeCancelObservation(),
      holding_decision: holdingDecision,
    } as unknown as DemoHistoryItem;
    items.push(item);
  }

  // 按日期倒序，与真实接口一致
  items.sort((a, b) => b.trade_date.localeCompare(a.trade_date));

  const tradeCount = items.filter((it) => ["success", "failed"].includes(it.outcome?.outcome_label ?? "")).length;
  const winCount = items.filter((it) => it.outcome?.outcome_label === "success").length;
  const cancelledCount = items.filter((it) => it.execution_state?.cancelled_before_entry).length;
  const returns = items
    .filter((it) => ["success", "failed"].includes(it.outcome?.outcome_label ?? ""))
    .map((it) => it.outcome?.executed_return ?? 0);
  const avgReturn = returns.length ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
  const totalReturn = returns.reduce((a, b) => a + b, 0);

  return {
    items,
    total: count,
    offset: 0,
    limit: count,
    facets: { setup_types: SETUP_TYPES.filter(Boolean) },
    summary: {
      trade_count: tradeCount,
      win_count: winCount,
      win_rate: tradeCount ? (winCount / tradeCount) * 100 : 0,
      average_net_return: avgReturn - 0.07,
      total_net_return: totalReturn - tradeCount * 0.07,
      additional_cost_pct: 0.07,
      cancel_observation: {
        cancelled_count: cancelledCount,
        observed_1d_count: randInt(0, cancelledCount),
        average_1d_return: cancelledCount ? randInt(-30, 30) / 10 : null,
        observed_3d_count: randInt(0, cancelledCount),
        average_3d_return: cancelledCount ? randInt(-40, 40) / 10 : null,
        observed_5d_count: randInt(0, cancelledCount),
        average_5d_return: cancelledCount ? randInt(-50, 50) / 10 : null,
        avoided_loss_count_5d: randInt(0, Math.max(1, cancelledCount)),
        avoided_loss_rate_5d: cancelledCount ? randInt(20, 80) : null,
        missed_gain_count_5d: randInt(0, cancelledCount),
      },
    },
  };
}

export type HistoryViewModel = ReturnType<typeof generateMockHistory>;

/** holding_decision code → 展示语气（用于配色） */
export function holdingDecisionTone(code?: string | null): "hold" | "reduce" | "exit" | "observe" | "none" {
  const raw = String(code ?? "").trim().toLowerCase();
  if (raw === "hold") return "hold";
  if (raw === "reduce") return "reduce";
  if (raw === "exit") return "exit";
  if (raw === "observe") return "observe";
  return "none";
}

/** holding_decision code → 中文动作名（兜底） */
export function holdingDecisionDefaultLabel(code?: string | null): string {
  const raw = String(code ?? "").trim().toLowerCase();
  const map: Record<string, string> = {
    hold: "继续持有",
    reduce: "收紧保护",
    exit: "退出持仓",
    observe: "等待数据",
  };
  return map[raw] || "—";
}
