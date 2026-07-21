/**
 * formal score_line 硬地板 —— 与 backend FORMAL_SCORE_LINE_FLOOR 对齐。
 *
 * 背景（self-improve 第 N 轮 T3）：matrix/手动把 score_line=55/60 写入 formal run，
 * 产出低质量票污染 D 项（历史 66 笔 WR 63.6%，剔除后 +2.3pp）。契约层已拦截新污染，
 * 但历史 run 仍落库。前端在记录中心/驾驶舱把 formal 低线 run 显式标出，避免运营者
 * 把历史低线票当「当前正式门槛下的表现」读。
 *
 * experiment / backtest 不受限（研究需要降线扫参）。
 */
export const FORMAL_SCORE_LINE_FLOOR = 72;

/** formal 且 score_line < 地板 → 历史低线污染（契约已拦新污染，旧票只标不重写）。 */
export function isLowFormalScoreLine(
  runMode: string | null | undefined,
  scoreLine: number | null | undefined,
): boolean {
  if (runMode !== "formal") return false;
  if (scoreLine == null || Number.isNaN(Number(scoreLine))) return false;
  return Number(scoreLine) < FORMAL_SCORE_LINE_FLOOR;
}

/** 分数线展示文案 + 是否污染 + tooltip。 */
export function describeScoreLine(
  scoreLine: number,
  runMode?: string | null,
): { text: string; polluted: boolean; title: string } {
  const polluted = isLowFormalScoreLine(runMode, scoreLine);
  const text = Number.isFinite(scoreLine) ? scoreLine.toFixed(1) : "—";
  if (!polluted) {
    return { text, polluted: false, title: `分数线 ${text}` };
  }
  return {
    text,
    polluted: true,
    title:
      `formal 分数线 ${text} < 地板 ${FORMAL_SCORE_LINE_FLOOR}，属 matrix/手动降线历史污染，` +
      `不代表当前正式门槛（契约层已拦截新污染）。D 项下滑先查是否低线污染而非策略退化。`,
  };
}
