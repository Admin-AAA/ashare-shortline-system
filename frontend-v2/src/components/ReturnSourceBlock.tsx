import type { CSSProperties } from "react";

/** §282/§285 收益来源指标（只展示，不参与选股/判定）：正常水平（两头各去 5% 后平均，标签依据）、压力测试（只去最赚 5% 后平均）、最赚 10 笔占总利润比例、中位数、最长连亏、稳健/彩票标签、新标准结论。 */
export type ReturnSourceWindow = {
  n?: number; avg?: number; pf?: number; mdd?: number; streak?: number; median?: number; trim5?: number; trim_both5?: number;
  top10_share?: number | null; top10_red?: boolean; label?: string; robust?: boolean;
};
export type ReturnSourceQuality = {
  standard?: string; basis?: string; R12?: ReturnSourceWindow; N3?: ReturnSourceWindow; label?: string;
  pass?: boolean; fail?: string[]; pos_phases?: number; n_phases?: number; bear?: { W0?: number | null; W00?: number | null };
};

const FAIL_TEXT: Record<string, string> = {
  "1_avg_pos": "平均每笔不为正", "2a_pf": "盈亏比<1.3", "2b_mdd": "最大回撤超上限",
  "3a_phases": "为正的阶段不够", "3b_bear": "熊市段亏损超 5%", "4_n": "样本不足",
};
const muted: CSSProperties = { fontSize: "var(--text-xs)", color: "var(--muted)", fontFamily: "var(--font-mono)" };
const pct = (v?: number | null, d = 2) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`);

function Row({ title, w }: { title: string; w?: ReturnSourceWindow }) {
  if (!w) return null;
  const red = w.top10_red;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))", gap: 6, alignItems: "baseline" }}
      title={`${title}：${w.label ?? ""}\n正常水平=去掉最赚 5% 和最亏 5% 的笔后，平均每笔收益（稳健/彩票标签按它判定：正常水平>0 且中位数>0 才算稳健）\n压力测试=只去掉最赚 5% 的笔后平均每笔（模拟「少数暴涨没抓到」，更苛刻）\n前10笔占比=最赚 10 笔收益之和÷全部笔收益之和（>50% 标红，说明利润靠少数几笔）；中位数=最典型一笔的收益`}>
      <span style={muted}><b>{title}</b></span>
      <span style={muted}>正常水平 <b style={{ color: (w.trim_both5 ?? 0) >= 0 ? "var(--market-up)" : "var(--market-down)" }}>{pct(w.trim_both5)}</b></span>
      <span style={muted}>压力测试 <b style={{ color: (w.trim5 ?? 0) >= 0 ? "var(--market-up)" : "var(--market-down)" }}>{pct(w.trim5)}</b></span>
      <span style={muted}>前10笔占比 <b style={{ color: red ? "var(--market-down)" : "var(--text)" }}>{w.top10_share == null ? "—" : `${Math.round(w.top10_share * 100)}%`}</b></span>
      <span style={muted}>中位数 <b style={{ color: (w.median ?? 0) >= 0 ? "var(--market-up)" : "var(--market-down)" }}>{pct(w.median)}</b></span>
      <span style={muted}>最长连亏 <b>{w.streak != null ? Math.round(w.streak) : "—"}</b></span>
    </div>
  );
}

export function ReturnSourceBlock({ quality }: { quality?: ReturnSourceQuality | null }) {
  if (!quality || !quality.R12) return null;
  const robust = (quality.label ?? "").startsWith("稳健");
  const tone = robust ? "var(--market-up)" : "var(--amber, #d97706)";
  const reasons = (quality.fail ?? []).map((k) => FAIL_TEXT[k] ?? k).join("、");
  return (
    <div style={{ marginTop: "var(--space-3)", paddingTop: "var(--space-3)", borderTop: "1px dashed var(--border)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text)", fontWeight: 600 }}>收益来源（钱是怎么赚的）</span>
        <span style={{ fontSize: "var(--text-xs)", fontWeight: 600, color: tone }} title="判定：两头各去 5% 后的平均每笔>0，且中位数>0（近12个月、近3年都满足才是稳健型）">{quality.label}</span>
      </div>
      <div style={{ display: "grid", gap: 4, marginTop: "var(--space-2)" }}>
        <Row title="近12个月" w={quality.R12} />
        <Row title="近3年" w={quality.N3} />
      </div>
      <div style={{ ...muted, marginTop: 4 }} title={quality.basis}>
        新标准（{quality.standard ?? "§282"}）：{quality.pass ? "达标" : `未达标（${reasons}）`}
        {quality.pos_phases != null ? ` · ${quality.pos_phases}/${quality.n_phases} 段为正` : ""}
        {quality.bear ? ` · 熊市段 ${pct(quality.bear.W0, 1)} / ${pct(quality.bear.W00, 1)}` : ""}
      </div>
    </div>
  );
}
