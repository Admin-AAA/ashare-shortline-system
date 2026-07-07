/**
 * 真实渲染验证：用 Playwright 启动 chromium，拦截 /api/auth/status 与
 * /api/runs/matrix/status，以「本次真实矩阵运行（matrix_20260706_010109）」
 * 的数据结构渲染 MatrixPage，真机截图证明 UI 正确（非 bug）。
 *
 * 数据来源：用户提供的 8 预设扫描/评分/推荐 + 15 只 ≥3 策略命中观察池。
 * 其中前 6 只（深南电路/铖昌科技/蓝思科技/普冉股份/维峰电子/聚辰股份）及
 * 部分候选的行业/分数/命中策略为真实数据；其余 9 只采用代表性数值（UI 校验用）。
 */
const { chromium } = require("playwright");

const WATCH = (code, name, industry, final_score, line, hits) => ({
  code, name, industry, final_score, effective_score_line: line,
  watchlist_gap: Math.round((line - final_score) * 100) / 100, hit_strategies: hits,
});

// 15 只 ≥3 策略命中观察池（被系统判定为最接近过线、多策略共振的候选）
const aggregated_watchlist = [
  WATCH("002916", "深南电路", "元器件", 86.35, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("001270", "铖昌科技", "半导体", 85.96, 115.0, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("300433", "蓝思科技", "元器件", 85.03, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("688766", "普冉股份", "半导体", 84.54, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("301328", "维峰电子", "元器件", 83.55, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("688123", "聚辰股份", "半导体", 83.41, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("688636", "智明达", "元器件", 83.51, 112.5, ["趋势龙头", "陈小群"]),
  WATCH("002841", "视源股份", "元器件", 82.70, 112.5, ["趋势龙头", "陈小群"]),
  WATCH("002600", "领益智造", "元器件", 81.55, 115.0, ["陈小群", "主线相对强度"]),
  WATCH("688141", "杰华特", "半导体", 81.47, 115.0, ["陈小群"]),
  // —— 以下为代表性数值（UI 校验用，非真实逐只明细）——
  WATCH("688010", "国力电子", "元器件", 80.10, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("688375", "国博电子", "半导体", 79.40, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("300661", "圣邦股份", "半导体", 78.20, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
  WATCH("603068", "博通集成", "半导体", 77.00, 115.0, ["陈小群", "主线相对强度"]),
  WATCH("688629", "华丰科技", "元器件", 76.30, 112.5, ["趋势龙头", "陈小群", "主线相对强度"]),
];

const runs = [
  { preset_id: "trend_leader", preset_name: "趋势龙头组合", run_id: "r1", status: "completed", recommended_count: 0, universe_count: 626, scored_count: 60, effective_score_line: 112.5, market_base: "强牛", market_sentiment: "高潮", top_candidate_score: 86.35 },
  { preset_id: "chen_xiaoqun", preset_name: "陈小群", run_id: "r2", status: "completed", recommended_count: 0, universe_count: 259, scored_count: 44, effective_score_line: 115.0, market_base: "强牛", market_sentiment: "高潮", top_candidate_score: 85.96 },
  { preset_id: "relative_strength", preset_name: "主线相对强度", run_id: "r3", status: "completed", recommended_count: 0, universe_count: 67, scored_count: 20, effective_score_line: 85.0, market_base: "强牛", market_sentiment: "高潮", top_candidate_score: 85.03 },
  { preset_id: "dsa", preset_name: "DSA", run_id: "r4", status: "completed", recommended_count: 0, universe_count: 586, scored_count: 14, effective_score_line: 91.0, market_base: "强牛", market_sentiment: "高潮", top_candidate_score: 84.54 },
  { preset_id: "limit_up_reseal", preset_name: "涨停回封接力", run_id: "r5", status: "completed", recommended_count: 0, universe_count: 36, scored_count: 14, effective_score_line: 82.0, market_base: "强牛", market_sentiment: "高潮", top_candidate_score: 83.55 },
  { preset_id: "overnight", preset_name: "隔夜持股", run_id: "r6", status: "completed", recommended_count: 0, universe_count: 36, scored_count: 8, effective_score_line: 91.0, market_base: "强牛", market_sentiment: "高潮", top_candidate_score: 81.55 },
  { preset_id: "new_high_breakout", preset_name: "阶段新高突破", run_id: "r7", status: "completed", recommended_count: 0, universe_count: 36, scored_count: null, effective_score_line: 85.0, market_base: "强牛", market_sentiment: "高潮" },
  { preset_id: "strong_trend_pullback", preset_name: "强趋势缩量回踩", run_id: "r8", status: "completed", recommended_count: 0, universe_count: 36, scored_count: null, effective_score_line: 82.0, market_base: "强牛", market_sentiment: "高潮" },
];

const FIXTURE = {
  task_run: {
    task_run_id: "matrix_20260706_010109",
    status: "completed",
    summary: "矩阵批次 8/8 预设完成，0 失败，推荐合计 0 条",
    result: {
      matrix_batch_id: "matrix_20260706_010109",
      matrix_batch_name: "预设矩阵 · 数据截至 2026-07-03",
      preset_count: 8,
      run_count: 8,
      completed_count: 8,
      failed_count: 0,
      recommended_count: 0,
      market_regime: "强牛·高潮",
      market_base: "强牛",
      market_sentiment: "高潮",
      top_candidate_score: 86.35,
      all_zero: true,
      runs,
      aggregated_watchlist,
    },
  },
};

const OUT = "/Users/wangao/Desktop/ashare-shortline-system/output";

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--no-proxy-server"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();

  // 绕过登录墙 + 注入真实结构的矩阵结果
  await page.route("**/api/auth/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ enabled: false, authenticated: true }) })
  );
  await page.route("**/api/runs/matrix/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(FIXTURE) })
  );

  console.log("navigating to /matrix ...");
  await page.goto("http://localhost:5174/matrix", { waitUntil: "domcontentloaded", timeout: 30000 });

  // 等待过热状态卡渲染出来
  await page.waitForSelector("text=市场过热", { timeout: 15000 }).catch(() => console.log("warn: 过热卡未出现，仍继续截图"));
  await page.waitForTimeout(900);

  // 默认主题（深色）先截一张全页
  const defaultTheme = await page.evaluate(() =>
    document.documentElement.getAttribute("data-theme") || "unknown"
  );
  console.log(`default theme: ${defaultTheme}`);
  const suffixDark = defaultTheme === "dark" ? "" : "_alt";
  const suffixLight = defaultTheme !== "dark" ? "" : "_alt";

  await page.screenshot({ path: `${OUT}/matrix_overheating${suffixDark}_full.png`, fullPage: true });
  await page.screenshot({ path: `${OUT}/matrix_overheating${suffixDark}_top.png` });
  console.log(`${defaultTheme} screenshots saved`);

  // 切换到另一套主题（JS 在页面 DOM 中直接找按钮，最稳）
  const toggled = await page.evaluate(() => {
    const btn = document.querySelector('button[aria-label*="切换"]')
      || Array.from(document.querySelectorAll('button.icon-button')).pop();
    if (!btn) return false;
    btn.click();
    return true;
  });
  if (!toggled) console.log("warn: could not find theme toggle button");
  await page.waitForTimeout(800);
  const flipped = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  console.log(`flipped theme: ${flipped}`);
  await page.screenshot({ path: `${OUT}/matrix_overheating${suffixLight}_full.png`, fullPage: true });
  console.log(`${flipped} screenshot saved`);

  await browser.close();
  console.log("SHOT_DONE");
})().catch((e) => {
  console.error("SHOT_FAILED:", e);
  process.exit(1);
});
