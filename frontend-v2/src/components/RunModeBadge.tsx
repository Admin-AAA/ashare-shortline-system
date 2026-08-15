/** Run 模式徽章 —— formal(实盘) 强调色，其他(回测等) muted。
 *  北极星 A 项强调 formal 推荐，所有 run 展示处（LatestRunCard / RunRecordsList）一屏可辨实盘 vs 回测。
 *  被 HomePanels.LatestRunCard 与 RecordsPage.RunRecordsList 共用。
 *  仅 run_mode=formal 且 evaluation_scope=historical_replay（REPLAY/历史补跑）时显示「回放」，
 *  避免把回放验证误认为实盘推荐（L-051）；experiment/backtest 保持各自标签。 */
const RUN_MODE_LABELS: Record<string, string> = {
  formal: "实盘",
  experiment: "实验",
  backtest: "回测",
};

export function RunModeBadge({
  runMode,
  evaluationScope,
}: {
  runMode?: string | null;
  evaluationScope?: string | null;
}) {
  if (!runMode) return null;
  const isReplay = runMode === "formal" && evaluationScope === "historical_replay";
  const isFormal = runMode === "formal" && !isReplay;
  // 回放 run 显示「回放」而非「实盘」，弱化强调色
  const label = isReplay ? "回放" : RUN_MODE_LABELS[runMode] ?? runMode;
  const color = isFormal ? "var(--accent-strong, var(--accent))" : "var(--muted)";
  return (
    <span
      title={
        isReplay
          ? "历史回放/补跑验证，非实盘推荐"
          : isFormal
            ? "实盘正式推荐"
            : label
      }
      style={{
        fontSize: 10,
        padding: "1px 6px",
        borderRadius: 3,
        fontFamily: "var(--font-mono)",
        fontWeight: 600,
        color,
        border: `1px solid ${color}40`,
        background: "transparent",
        letterSpacing: 0.5,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}
