/** Run 模式徽章 —— formal(实盘) 强调色，其他(回测等) muted。
 *  北极星 A 项强调 formal 推荐，所有 run 展示处（LatestRunCard / RunRecordsList）一屏可辨实盘 vs 回测。
 *  被 HomePanels.LatestRunCard 与 RecordsPage.RunRecordsList 共用。 */
export function RunModeBadge({ runMode }: { runMode?: string | null }) {
  if (!runMode) return null;
  const isFormal = runMode === "formal";
  const color = isFormal ? "var(--accent-strong, var(--accent))" : "var(--muted)";
  return (
    <span
      title={isFormal ? "实盘正式推荐" : runMode}
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
      {isFormal ? "实盘" : runMode.toUpperCase()}
    </span>
  );
}
