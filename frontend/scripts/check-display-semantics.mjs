import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function assertNotContains(text, needle, message) {
  assert(!text.includes(needle), message);
}

const server = await createServer({
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
});

try {
  const display = await server.ssrLoadModule("/src/utils/display.ts");
  const { default: MarketTemperaturePanel } = await server.ssrLoadModule(
    "/src/features/dashboard/components/MarketTemperaturePanel.tsx",
  );
  const { default: DragonCandidatesPanel } = await server.ssrLoadModule(
    "/src/features/dashboard/components/DragonCandidatesPanel.tsx",
  );
  const { default: MetricsPanel } = await server.ssrLoadModule(
    "/src/features/dashboard/components/MetricsPanel.tsx",
  );

  assert(display.aShareDirectionClass(1) === "market-up", "A股上涨/正收益必须使用红色 market-up");
  assert(display.aShareDirectionClass(-1) === "market-down", "A股下跌/负收益必须使用绿色 market-down");
  assert(display.aShareDirectionClass(0) === "market-flat", "零值必须使用中性颜色");

  assert(display.businessStatusLabel("READY") === "本地池可推荐", "READY 不能裸露在中文界面");
  assert(display.businessStatusLabel("CHECK") === "数据需检查", "CHECK 不能裸露在中文界面");
  assert(display.businessStatusLabel("failed") === "运行失败，可补数后重试", "failed 不能裸露在中文界面");
  assert(display.businessStatusLabel("risk") === "存在风险，需复核", "risk 不能裸露在中文界面");
  assert(
    display.businessStatusLabel("insufficient_minute_coverage") === "分钟线不足，暂停回测",
    "分钟线门禁状态必须是业务语言",
  );
  assert(display.businessStatusLabel("not_attempted") === "未调用，已有本地数据", "补数状态不能裸露英文码");

  const failedEmpty = display.candidateEmptyState({
    recommendations: [],
    current_run: {
      run_id: "run_failed",
      status: "failed",
      phase: "rule_scan",
      heartbeat_stale: false,
      message: "模拟失败",
    },
    data_health: {
      can_run_local_recommendation: true,
      status: "ready",
      missing_bar_count: 0,
      short_history_count: 0,
      eligible_bar_coverage_pct: 100,
    },
  });
  assert(failedEmpty.title === "本次推荐没有可用候选", "失败空状态标题必须解释 0 只候选");
  assert(failedEmpty.reason === "运行失败，可补数后重试", "失败空状态必须用业务语言");
  assert(failedEmpty.detail.includes("当前 0 只不能视为市场无机会"), "失败空状态必须避免误读为无机会");

  const marketHtml = renderToStaticMarkup(
    React.createElement(MarketTemperaturePanel, {
      market: {
        heat_score: 62.4,
        regime: "risk",
        sample_count: 4200,
        trade_date: "2026-05-27",
        up_count: 2100,
        down_count: 1800,
        up_ratio: 0.5,
        limit_up_count: 55,
        limit_down_count: 12,
        main_industries: ["机器人"],
      },
    }),
  );
  assert(marketHtml.includes('class="market-up">2100</strong>'), "上涨家数必须使用 market-up");
  assert(marketHtml.includes('class="market-down">1800</strong>'), "下跌家数必须使用 market-down");
  assert(marketHtml.includes("存在风险，需复核"), "市场状态 risk 必须翻译成业务语言");
  assertNotContains(marketHtml, ">risk<", "市场状态不能裸露 risk");

  const emptyHtml = renderToStaticMarkup(
    React.createElement(DragonCandidatesPanel, {
      items: [],
      emptyState: failedEmpty,
    }),
  );
  assert(emptyHtml.includes("本次推荐没有可用候选"), "空候选必须展示归因标题");
  assert(emptyHtml.includes("当前 0 只不能视为市场无机会"), "空候选必须展示误读保护说明");
  assertNotContains(emptyHtml, "暂无推荐结果", "空候选不能回退为泛化空状态");

  const fallbackEmptyHtml = renderToStaticMarkup(
    React.createElement(DragonCandidatesPanel, {
      items: [],
    }),
  );
  assert(fallbackEmptyHtml.includes("缺少运行归因"), "无归因空状态也必须提示不可直接解读为无机会");
  assertNotContains(fallbackEmptyHtml, "暂无推荐结果", "无归因空状态不能使用泛化空文案");

  const metricsHtml = renderToStaticMarkup(
    React.createElement(MetricsPanel, {
      loading: false,
      metrics: {
        total_recommendations: 35,
        recommendation_accuracy: 0.57,
        average_net_return: 1.23,
        median_net_return: -0.45,
        profit_loss_ratio: 1.7,
        max_drawdown: -8,
        positive_month_ratio: 0.6,
        benchmark_excess_return: -0.32,
        strategy_compound_return: 4.5,
        benchmark_compound_return: -1.1,
        sample_confidence: "normal",
        accuracy_numerator: 20,
        accuracy_denominator: 35,
        trade_accuracy: 0.62,
        executed_trade_count: 31,
        unexecuted_count: 4,
        trigger_rate: 0.89,
        avg_holding_days: 2.2,
        false_positive_notes: [],
        false_positive_category_counts: [],
      },
    }),
  );
  assert(metricsHtml.includes('class="kpi-value market-up">1.23%</span>'), "正收益必须使用 market-up");
  assert(metricsHtml.includes('class="market-down">-0.45%</strong>'), "负中位收益必须使用 market-down");
  assert(metricsHtml.includes('class="kpi-value market-down">-0.32%</span>'), "负超额收益必须使用 market-down");

  console.log("display semantics checks passed");
} finally {
  await server.close();
}
