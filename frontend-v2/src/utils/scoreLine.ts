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

/**
 * 单票分数线准入标记 —— 记录中心「股票分明细」一屏可见该票是否低于正式地板被放行。
 *
 * 与 run 级 ScoreLineCell（标记 run.score_line<72 的历史降线 run）互补：这里标记的是
 * **单票**——某条 formal 推荐虽最终分 < 正式地板 72，但因 dynamic soft floor（如 震荡期
 * 动态降线）被放行。运营者看到低分 formal 票时，能区分：
 *   - 软降线准入：设计内的动态降线放行（info 色，附 reason）
 *   - 低线：run 级降线（warning 色，已在 run 头标过，这里兜底）
 * 单票无 run_mode 字段，仅凭 score_line_meta.dynamic_soft_floor_applied 判定，
 * experiment/backtest 不受限（研究需要）。
 */
export type TicketScoreFlag =
  | { kind: "soft_floor"; title: string }
  | { kind: "low_line"; title: string };

export function ticketScoreFlag(
  finalScore: number | null | undefined,
  scoreLineMeta: Record<string, unknown> | null | undefined,
): TicketScoreFlag | null {
  // 仅对低于正式地板的 formal 单票标徽——高于地板的票（即便 soft floor 机制参与，
  // 也可能只是动态抬线）不标，避免误把 74.4<72 这类错误文案渲染出来。
  const belowFloor = typeof finalScore === "number" && finalScore < FORMAL_SCORE_LINE_FLOOR;
  if (!belowFloor) return null;

  const applied = scoreLineMeta?.dynamic_soft_floor_applied;
  const soft =
    applied === true ||
    applied === 1 ||
    applied === "1" ||
    applied === "true" ||
    applied === "True";
  if (soft) {
    const reason = scoreLineMeta?.soft_floor_reason;
    return {
      kind: "soft_floor",
      title:
        `最终分 ${finalScore.toFixed(1)} < 正式地板 ${FORMAL_SCORE_LINE_FLOOR}，` +
        `动态降线（soft floor）放行` +
        (reason ? `：${String(reason)}` : "（设计内动态降线机制，非 matrix/手动降线 run）"),
    };
  }
  return {
    kind: "low_line",
    title:
      `最终分 ${finalScore.toFixed(1)} < 正式地板 ${FORMAL_SCORE_LINE_FLOOR}，` +
      `历史低分 formal 票（run 级已标「低线」，不代表当前门槛）`,
  };
}
