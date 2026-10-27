import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import "../styles/quality.css";

/** 后端 quality 块（短线 GET /api/strategies、中长线 GET /api/horizons/{h}/strategies 的 metrics.quality）。 */
export type QualityWindow = { n?: number; avg?: number; median?: number; trim_both5?: number; pf?: number; mdd?: number; label?: string };
export type StrategyQuality = { label?: string; label_rule?: string; R12?: QualityWindow; N3?: QualityWindow };

/** 前瞻/回放已结算统计（短线 /api/metrics/by-strategy；中长线 /api/horizons/{h}/metrics by_strategy） */
export type RealizedStats = { n?: number | null; avg?: number | null; median?: number | null };

export const MIN_SAMPLE = 30;
const FALLBACK_RULE = "近12个月与 2024-01 至今两段都满足「两头各去5%后均值>0 且 中位数>0」为稳健型，否则为彩票型。";

const fmt = (v?: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const cls = (v?: number | null) => (v == null ? "" : v > 0 ? "qs__up" : v < 0 ? "qs__down" : "");

/** 标签文字直接取后端 quality.label（如「稳健型：多数交易都赚一点」）；这里只按前缀决定颜色，不做任何判定。 */
export function QualityChip({ quality }: { quality?: StrategyQuality | null }) {
  const label = quality?.label;
  if (!label) return <span className="qchip qchip--none">—</span>;
  const short = label.split("：")[0];
  const kind = label.startsWith("彩票") ? "lottery" : label.startsWith("稳健") ? "steady" : "none";
  const n = (w?: QualityWindow) => (w ? `${w.label?.split("：")[0] ?? "—"}（n=${w.n ?? "—"}，均值 ${fmt(w.avg)}，中位数 ${fmt(w.median)}，去极值均值 ${fmt(w.trim_both5)}）` : "—");
  const tip = `${label}\n近12个月：${n(quality.R12)}\n2024-01至今：${n(quality.N3)}\n规则：${quality.label_rule ?? FALLBACK_RULE}\n标签来自回测，不是前瞻结果。`;
  return <span className={`qchip qchip--${kind}`} title={tip}>{short}</span>;
}


function Pair({ label, tip, avg, median, extra, n, low }: { label: string; tip: string; avg?: number | null; median?: number | null; extra?: React.ReactNode; n?: number | null; low?: boolean }) {
  return (
    <div className={`qs__g${low ? " qs__g--low" : ""}`} title={tip}>
      <span className="qs__l">{label}</span>
      <span className="qs__v">
        {low ? <b>{n ? "样本不足" : "暂无"}</b> : <b className={cls(avg)}>{fmt(avg)}</b>}
        {!low && <i>中 <span className={cls(median)}>{fmt(median)}</span></i>}
      </span>
      <span className="qs__s">{extra}{n != null && <em>{n} 笔</em>}</span>
    </div>
  );
}

/** 策略画像条：标签 + 近12个月 / 2024-01至今 / 已结算（前瞻或回放）。放在策略行或策略卡上，全部为后端字段，前端不计算标签。
 * realizedKind：前瞻 = 真实前瞻已结算；回放 = 历史回放（中长线目前真实前瞻 0 笔）。 */
export function QualityStrip({ quality, realized, realizedKind = "前瞻", style }: { quality?: StrategyQuality | null; realized?: RealizedStats | null; realizedKind?: "前瞻" | "回放"; style?: React.CSSProperties }) {
  const a = quality?.R12, b = quality?.N3;
  if (!quality && !realized) return <div className="qs" style={style}><QualityChip quality={null} /><span className="qs__none">暂无回测画像</span></div>;
  const rn = realized?.n ?? 0;
  const low = rn < MIN_SAMPLE;
  const realizedTip = realizedKind === "回放"
    ? `回放已平仓：用当前规则回放历史行情得到，不代表实盘前瞻表现（真实前瞻已平仓 0 笔）。${low ? `n=${rn} < ${MIN_SAMPLE}，样本不足` : ""}`
    : `前瞻已结算（仅实盘）。${low ? `n=${rn} < ${MIN_SAMPLE}：平均 ${fmt(realized?.avg)}，中位数 ${fmt(realized?.median)}，样本太少仅供参考` : ""}`;
  const warn = !!quality?.label?.startsWith("稳健") && !low && (realized?.median ?? 0) < 0;
  return (
    <div className="qs" style={style}>
      <div className="qs__chipcol"><QualityChip quality={quality} /></div>
      <Pair label="近12个月" tip={`回测，已扣 0.3% 成本，每笔平均 / 中位数 / 笔数\n两头各去5%后的均值 ${fmt(a?.trim_both5)}`} avg={a?.avg} median={a?.median} n={a?.n} />
      <Pair label="2024-01 至今" tip={`2024-01 至今（约2.8年）回测，已扣 0.3% 成本，每笔平均 / 中位数 / 笔数`} avg={b?.avg} median={b?.median} n={b?.n}
        extra={<i title="两头各去 5% 后的平均">去极值 <span className={cls(b?.trim_both5)}>{fmt(b?.trim_both5)}</span></i>} />
      <Pair label={realizedKind === "回放" ? "回放已平仓" : "前瞻已结算"} tip={realizedTip} avg={realized?.avg} median={realized?.median} n={realized?.n ?? 0} low={low} />
      {warn && <span className="qchip qchip--lottery qs__warn" title="标签来自回测，但回放已平仓的中位数为负，请留意两者不一致。">回放中位数 {fmt(realized?.median)}</span>}
    </div>
  );
}

/** 短线：质量画像 + 前瞻已结算，取自 /api/strategies 与 /api/metrics/by-strategy（只读 GET，react-query 共享缓存）。 */
export function useShortQuality() {
  const qs = useQuery({ queryKey: ["strategy-quality"], queryFn: () => api.strategies() });
  const st = useQuery({ queryKey: ["by-strategy-review"], queryFn: () => api.metricsByStrategy(undefined, "live_forward") });
  return (id: string): { quality?: StrategyQuality | null; realized: RealizedStats | null } => {
    const r = st.data?.find((x) => x.strategy_id === id);
    return { quality: qs.data?.find((x) => x.skill_id === id)?.quality, realized: r ? { n: r.trade_count, avg: r.average_net_return, median: r.median_net_return } : null };
  };
}
export function ShortStrip({ id, style }: { id: string; style?: React.CSSProperties }) {
  const get = useShortQuality();
  const { quality, realized } = get(id);
  return <QualityStrip quality={quality} realized={realized} realizedKind="前瞻" style={style} />;
}
