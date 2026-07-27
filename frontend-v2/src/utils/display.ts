import type { DashboardOverview, DashboardDataHealth, RunProgress } from "../types";

export type Tone = "positive" | "warning" | "danger" | "neutral";

const API_DISPLAY_TIME_ZONE = "Asia/Shanghai";

const STATUS_LABELS: Record<string, string> = {
  READY: "本地池可推荐",
  CHECK: "数据需检查",
  unknown: "状态待确认",
  idle: "暂无运行",
  ready: "可运行",
  warning: "可运行但置信度低",
  risk: "存在风险，需复核",
  blocked: "数据不足，暂停推荐",
  completed: "已完成",
  failed: "失败",
  run_failed: "运行失败",
  outcome_failed: "复盘失败/止损触发",
  error: "运行出错，可查看原因后重试",
  cancelled: "已取消",
  skipped: "已跳过",
  precheck: "先验检查",
  queued: "排队中",
  running: "运行中",
  interrupted: "已中断，可从断点继续",
  stale: "疑似中断，建议恢复或重跑",
  no_data: "暂无样本",
  no_action: "无需处理",
  partial_completed: "部分完成，需复核",
  already_ready: "已满足门禁",
  partial_failed: "部分失败，可重试缺口",
  healthy: "健康",
  degraded: "降级可用",
  untested: "未验证",
  unavailable: "暂不可用",
  permission_denied: "权限不足，已走备用源",
  not_attempted: "未调用，已有本地数据",
  local_minute_db: "本地分钟库",
  local_minute_db_15min: "本地15分钟库",
  local_minute_db_1min: "本地1分钟库",
  empty: "暂无数据",
  ok: "正常",
  normal: "正常",
  low: "低置信度",
  high: "高风险",
  medium: "中等风险",
  none: "未关注",
  pending: "待复盘",
  watching: "已关注",
  ignored: "已忽略",
  missed: "未触发",
  success: "复盘成功",
  invalid: "信号失效",
  all: "全市场",
  watchlist: "自选股",
  industry: "行业",
  conservative: "保守",
  balanced: "均衡",
  aggressive: "激进",
  local_pool_preferred: "仅用本地池",
  auto: "本地池+盘中实时",
  data_sync: "数据同步",
  pool_refresh: "行情池刷新",
  data_backfill: "数据回填",
  recommendation_run: "股票推荐",
  preset_matrix_run: "预设矩阵",
  manual: "手动触发",
  schedule: "定时触发",
  api: "接口写入",
  prepare: "准备运行",
  starting: "启动中",
  market_data: "市场数据",
  scope_filter: "范围过滤",
  auxiliary_data: "辅助数据",
  feature_build: "特征构建",
  rule_scan: "规则扫描",
  skill_gate: "规则门",
  skill_review: "Skill 深审",
  aggregate: "聚合",
  persist: "保存结果",
  aggregate_metrics: "聚合指标",
  insufficient_minute_coverage: "分钟线不足，暂停回测",
};

// 退出原因（exit_reason）原始码 → 中文标签。
// 与 backend/app/services/outcome_service.py 的 _build_outcome_reason 标签对齐，
// 并补齐隔夜持仓 / trailing stop 等来源的值。复盘页与记录页共用，避免散落硬编码。
export const EXIT_REASON_LABELS: Record<string, string> = {
  take_profit_hit: "盘中触发止盈",
  take_profit_gap: "开盘跳空止盈",
  stop_loss_hit: "盘中触发止损",
  stop_loss_gap: "开盘跳空止损",
  stop_loss_intraday_both_hit: "同日触止盈止损（保守按止损）",
  horizon: "持有到观察期结束",
  horizon_close: "持有到观察期结束",
  horizon_close_t1_locked: "数据仅至入场日，待今日更新",
  limit_down_locked_horizon_mark: "一字跌停或无流动性（期末标记）",
  horizon_close_after_locked_limit_down: "跌停后递延至观察期结束",
  trailing_stop_hit: "移动止盈触发",
  trailing_stop_gap: "开盘跳空移动止盈",
  not_triggered: "未触发买点",
  entry_not_filled: "委托未成交",
  recommendation_execution_blocked: "推荐不可真实成交",
  gap_up_blocked: "高开未达成交条件",
  next_morning_exit: "次日开盘退出",
  one_word_limit_up_hold: "一字涨停继续持有",
  shrinking_limit_up_hold: "缩量涨停继续持有",
  deferred_after_limit_up: "涨停后递延退出",
};

// 策略 bucket（英文 kebab-case 标识）→ 中文名。
// 与 backend/skills/*/strategy.yaml 的 id↔name 对齐，避免复盘页买点列直接展示英文。
export const BUCKET_LABELS: Record<string, string> = {
  "daily-stock-analysis-skill": "基础短线策略",
  "chao-gu-yang-jia-skill": "炒股养家大局观",
  "chen-xiaoqun-skill": "陈小群龙头战法",
  "chip-single-peak-breakout": "筹码单峰突破",
  "high-position-volume-stall-warning": "高位放量滞涨预警",
  "high-range-breakout-momentum": "阶段新高放量突破",
  "small-cap-thematic-momentum": "小盘题材动量",
  "trend-pullback-continuation": "强趋势缩量回踩",
  "weak-market-strong-stock-reversal": "弱市强势股确认回撤",
  "sector-relative-strength-leader": "主线相对强度龙头",
  "overnight-holding-method": "隔夜持股法",
  "limit-up-reseal-relay": "涨停回封接力",
  "dragon-tiger-smart-money": "龙虎榜聪明钱接力",
};

export function bucketLabel(value: unknown, fallback = "—") {
  const raw = String(value ?? "").trim();
  if (!raw) return fallback;
  return BUCKET_LABELS[raw] ?? raw;
}

// setup_type 为空时的兜底文案。空值 = 没有任何策略命中入场 setup，
// 是"未触发买点"的复盘标记，不是一种买点类型；文案要避免被误读成买入信号。
// 历史数据曾将 Skill ID 误写入 setup_type：已知 ID 使用统一策略中文名；未知机器码
// 不直接暴露给用户，但保留 rawValue 供页面通过 title 辅助排查。
const MACHINE_CODE_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)+$/i;

export function setupLabel(value: unknown) {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return { label: "无明确买点 · 等待确认", isPlaceholder: true, rawValue: "" };
  }
  const strategyLabel = BUCKET_LABELS[raw];
  if (strategyLabel) {
    return { label: strategyLabel, isPlaceholder: false, rawValue: "" };
  }
  if (MACHINE_CODE_PATTERN.test(raw)) {
    return { label: "策略信号待确认", isPlaceholder: false, rawValue: raw };
  }
  return { label: raw, isPlaceholder: false, rawValue: "" };
}

export function exitReasonLabel(value: unknown, fallback = "—", executedReturn?: number | null) {
  const raw = String(value ?? "").trim();
  if (!raw) return fallback;
  const key = raw.toLowerCase();
  const label = EXIT_REASON_LABELS[key];
  // trailing_stop 是「移动止盈/止损」机制：激活条件是浮盈≥2%，但跳空低开+扣费后
  // 实际收益可能为负。根据真实收益动态调整文案，避免"止盈"却亏钱的矛盾感。
  if (
    (key === "trailing_stop_hit" || key === "trailing_stop_gap") &&
    executedReturn != null && !Number.isNaN(executedReturn) && executedReturn < 0
  ) {
    return key === "trailing_stop_hit" ? "移动止损触发" : "开盘跳空移动止损";
  }
  // 后端新增枚举时，不能将机器码直接暴露在用户界面；若后端已给出中文则原样保留。
  return label ?? (/^[\u4e00-\u9fff]/.test(raw) ? raw : "退出原因待确认");
}

// outcome_label 原始码 → 中文。统一详情页与历史页的标签文案，避免两处各自硬编码。
// provisional 是临时态：数据仅至入场日，executed_return 只是入场日收盘临时标记，
// 下一交易日数据到位后会重新结算为 success/failed，故显示「结算中」而非「失败」。
export function outcomeLabel(value: unknown, fallback = "—") {
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw) return fallback;
  const labels: Record<string, string> = {
    success: "成功",
    failed: "失败",
    missed: "未触发",
    pending: "待标注",
    provisional: "结算中",
  };
  return labels[raw] ?? raw;
}

// outcome_label 对应的语气色：provisional 用中性金（区别于 success 绿 / failed 红），
// 让「结算中」在视觉上不被误读成最终结论。
export function outcomeTone(value: unknown): Tone {
  const raw = String(value ?? "").trim().toLowerCase();
  if (raw === "success") return "positive";
  if (raw === "failed") return "danger";
  if (raw === "provisional") return "warning";
  return "neutral";
}

export function businessStatusLabel(value: unknown, fallback = "状态待确认") {
  const raw = String(value ?? "").trim();
  if (!raw) return fallback;
  return STATUS_LABELS[raw] ?? STATUS_LABELS[raw.toLowerCase()] ?? raw;
}

export function riskLevelLabel(value: unknown) {
  const key = String(value ?? "").trim().toLowerCase();
  const labels: Record<string, string> = {
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    unknown: "风险待确认",
  };
  return labels[key] ?? businessStatusLabel(value);
}

export function runStatusLabel(value: unknown, fallback = "状态待确认") {
  const key = String(value ?? "").trim().toLowerCase();
  if (key === "failed") return STATUS_LABELS.run_failed;
  return businessStatusLabel(value, fallback);
}

export function recommendationStatusLabel(value: unknown, fallback = "状态待确认") {
  const key = String(value ?? "").trim().toLowerCase();
  if (key === "failed") return STATUS_LABELS.outcome_failed;
  if (key === "none") return STATUS_LABELS.none;
  return businessStatusLabel(value, fallback);
}

export function watchStatusLabel(value: unknown, fallback = "未关注") {
  const key = String(value ?? "").trim().toLowerCase();
  if (!key || key === "none") return STATUS_LABELS.none;
  return businessStatusLabel(value, fallback);
}

export function outcomeStatusLabel(value: unknown, fallback = "-") {
  const key = String(value ?? "").trim().toLowerCase();
  if (key === "failed") return STATUS_LABELS.outcome_failed;
  return businessStatusLabel(value, fallback);
}

export function parseApiDateTime(value?: string | null) {
  if (!value) return null;
  const raw = value.trim();
  if (!raw) return null;
  const hasTimezone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(raw);
  const isNaiveDateTime = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(raw);
  const normalized = isNaiveDateTime && !hasTimezone ? `${raw.replace(" ", "T")}Z` : raw;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function apiTimestamp(value?: string | null) {
  return parseApiDateTime(value)?.getTime() ?? 0;
}

export function formatApiDateTime(
  value?: string | null,
  options?: Intl.DateTimeFormatOptions,
  fallback = "-",
) {
  if (!value) return fallback;
  const parsed = parseApiDateTime(value);
  if (!parsed) return value;
  return parsed.toLocaleString("zh-CN", {
    hour12: false,
    timeZone: API_DISPLAY_TIME_ZONE,
    ...options,
  });
}

export function statusTone(value: unknown): Tone {
  const key = String(value ?? "").trim().toLowerCase();
  if (["ready", "completed", "healthy", "ok", "normal"].includes(key)) return "positive";
  if (["failed", "error", "interrupted", "stale", "blocked", "unavailable", "high"].includes(key)) return "danger";
  if (["warning", "risk", "queued", "running", "manual", "schedule", "low", "degraded", "untested", "partial_completed"].includes(key)) return "warning";
  return "neutral";
}

export function readinessCopy(canRun: unknown, status?: unknown) {
  const ready = String(canRun ?? false) === "true";
  const rawStatus = String(status ?? "").trim().toLowerCase();
  if (ready && rawStatus === "warning") {
    return { value: "可运行", note: "可运行但置信度低，建议先看缺口样例" };
  }
  if (ready) {
    return { value: "可运行", note: "本地池满足推荐前置条件" };
  }
  if (rawStatus === "blocked") {
    return { value: "暂停推荐", note: "数据不足，先补齐日K/报价后再运行" };
  }
  return { value: "需检查", note: businessStatusLabel(status, "数据状态待确认") };
}

export function aShareDirectionClass(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "market-flat";
  return n > 0 ? "market-up" : "market-down";
}

export function aShareUpClass() {
  return "market-up";
}

export function aShareDownClass() {
  return "market-down";
}

// 统一的分数配色：全系统唯一入口（替代 4 处分裂的实现）
// 阈值依据：>=72 强势绿、>=50 中性金、>=30 警告橙、<30 危险红
// 颜色饱和度低，长时间盯盘不累（A 股交易驾驶舱审美标准）
export function scoreColor(score: number): string {
  if (score >= 72) return "#1a6b4a";
  if (score >= 50) return "#b8860b";
  if (score >= 30) return "#e67e22";
  return "#c0392b";
}

export function candidateEmptyState(data: DashboardOverview) {
  const run = data.current_run;
  const health = data.data_health;
  if (data.recommendations.length > 0) {
    return null;
  }
  if (run.status === "failed" || run.status === "interrupted" || run.heartbeat_stale) {
    const failureMessage = run.error_message || run.message;
    const normalizedFailureMessage = failureMessage.replace(/[。.!！?？]+$/, "");
    const failureDetail = failureMessage
      ? `${normalizedFailureMessage}。当前 0 只不能视为市场无机会。`
      : "运行未完整结束，当前 0 只不能视为市场无机会。";
    return {
      title: "本次推荐没有可用候选",
      reason: runStatusLabel(run.status),
      detail: failureDetail,
    };
  }
  if (!health.can_run_local_recommendation || health.status === "blocked") {
    return {
      title: "数据不足，暂停推荐",
      reason: readinessCopy(false, health.status).note,
      detail: summarizeDataHealthGaps(health),
    };
  }
  if (!run.run_id || run.status === "idle") {
    return {
      title: "还没有完成推荐运行",
      reason: "当前没有可复盘的最新运行。",
      detail: "先到推荐运行页触发一次推荐，系统会给出候选或筛空原因。",
    };
  }
  if (run.status === "queued" || run.status === "running") {
    return {
      title: "推荐仍在运行",
      reason: runStatusLabel(run.status),
      detail: run.message || "候选池还在生成，暂不能把 0 只解读成无机会。",
    };
  }
  return {
    title: "当前没有达标龙头候选",
    reason: "数据可用且运行已结束，但候选没有通过强度、风险或共识门槛。",
    detail: "这是一个有效的短线结论：今天先不强行出手，等待主线或承接更清晰。",
  };
}

function summarizeDataHealthGaps(health: DashboardDataHealth) {
  const missing = Number(health.missing_bar_count || 0);
  const shortHistory = Number(health.short_history_count || 0);
  const immatureHistory = Number(health.immature_history_count || 0);
  const coverage = health.eligible_bar_coverage_pct ?? health.bar_coverage_pct;
  const parts = [
    missing > 0 ? `缺日K ${missing} 只` : "",
    shortHistory > 0 ? `可补短历史 ${shortHistory} 只` : "",
    immatureHistory > 0 ? `上市未满30根 ${immatureHistory} 只` : "",
    coverage !== undefined && coverage !== null ? `合格日K覆盖 ${coverage}%` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "数据池未达到本地推荐门槛。";
}

export function runStatusCopy(run: RunProgress) {
  if (!run.run_id) return { status: "暂无运行", phase: "等待触发" };
  if (run.heartbeat_stale) return { status: "疑似中断", phase: "心跳过期，建议恢复或重跑" };
  return {
    status: runStatusLabel(run.status),
    phase: businessStatusLabel(run.phase || "idle"),
  };
}
