import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { realizedStripCopy, soldStats, type RealizedWindow } from "../utils/realizedStrip";
import type { ReturnSourceQuality } from "./ReturnSourceBlock";
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

/** 策略画像条：标签 + 近12个月 / 2024-01至今 / 已结算（前瞻或回放）。放在策略行或策略卡上，全部为后端字段，前端不计算标签、也不自己截窗口。
 * realizedKind：前瞻 = 真实前瞻已结算；回放 = 历史回放（中长线目前真实前瞻 0 笔）。
 * realizedWindow：中长线画像用后端 portrait_realized_12m 的起止；短线不传，前瞻文案保持原样。 */
export function QualityStrip({ quality, realized, realizedKind = "前瞻", realizedWindow, realizedCopy, replay, style }: {
  quality?: StrategyQuality | null; realized?: RealizedStats | null; realizedKind?: "前瞻" | "回放"; realizedWindow?: RealizedWindow | null;
  /** 覆盖第三格文案（短线用「实盘已卖出」）；不传沿用 realizedStripCopy */
  realizedCopy?: { label: string; tip: string };
  /** 短线专用：回放已卖出单独一格，与实盘分开，不混算 */
  replay?: { stats: RealizedStats | null; label: string; tip: string };
  style?: React.CSSProperties;
}) {
  const a = quality?.R12, b = quality?.N3;
  if (!quality && !realized && !replay?.stats) return <div className="qs" style={style}><QualityChip quality={null} /><span className="qs__none">暂无回测画像</span></div>;
  const rn = realized?.n ?? 0;
  const low = rn < MIN_SAMPLE;
  const lowNote = !low ? "" : realizedKind === "回放"
    ? ` n=${rn} < ${MIN_SAMPLE}，样本不足`
    : ` n=${rn} < ${MIN_SAMPLE}：平均 ${fmt(realized?.avg)}，中位数 ${fmt(realized?.median)}，样本太少仅供参考`;
  const base = realizedStripCopy(realizedKind, realizedWindow, lowNote);
  const copy = realizedCopy ? { label: realizedCopy.label, tip: realizedCopy.tip + lowNote } : base;
  const pn = replay?.stats?.n ?? 0;
  const replayLow = pn < MIN_SAMPLE;
  const replayNote = replayLow ? ` n=${pn} < ${MIN_SAMPLE}：平均 ${fmt(replay?.stats?.avg)}，中位数 ${fmt(replay?.stats?.median)}，样本太少仅供参考` : "";
  const steady = !!quality?.label?.startsWith("稳健");
  const warn = steady && !low && (realized?.median ?? 0) < 0;
  const warnTitle = realizedCopy
    ? `标签来自回测，但「${realizedCopy.label}」的中位数为负，请留意两者不一致。`
    : realizedKind === "回放"
      ? "标签来自回测，但近12个月回放已平仓的中位数为负，请留意两者不一致。与研究「近12个月」的数字不必相同。"
      : "标签来自回测，但回放已平仓的中位数为负，请留意两者不一致。";
  const warnLabel = realizedCopy ? realizedCopy.label : "回放";
  const replayWarn = steady && !!replay && !replayLow && (replay.stats?.median ?? 0) < 0;
  return (
    <div className="qs" style={style}>
      <div className="qs__chipcol"><QualityChip quality={quality} /></div>
      <Pair label="近12个月" tip={`回测，已扣 0.3% 成本，每笔平均 / 中位数 / 笔数\n两头各去5%后的均值 ${fmt(a?.trim_both5)}`} avg={a?.avg} median={a?.median} n={a?.n} />
      <Pair label="2024-01 至今" tip={`2024-01 至今（约2.8年）回测，已扣 0.3% 成本，每笔平均 / 中位数 / 笔数`} avg={b?.avg} median={b?.median} n={b?.n}
        extra={<i title="两头各去 5% 后的平均">去极值 <span className={cls(b?.trim_both5)}>{fmt(b?.trim_both5)}</span></i>} />
      <Pair label={copy.label} tip={copy.tip} avg={realized?.avg} median={realized?.median} n={realized?.n ?? 0} low={low} />
      {replay && <Pair label={replay.label} tip={replay.tip + replayNote} avg={replay.stats?.avg} median={replay.stats?.median} n={pn} low={replayLow} />}
      {warn && <span className="qchip qchip--lottery qs__warn" title={warnTitle}>{warnLabel}中位数 {fmt(realized?.median)}</span>}
      {replayWarn && <span className="qchip qchip--lottery qs__warn" title={`标签来自回测，但「${replay!.label}」的中位数为负，请留意两者不一致。`}>回放中位数 {fmt(replay!.stats?.median)}</span>}
    </div>
  );
}

export const SHORT_LIVE_COPY = {
  label: "实盘已卖出",
  tip: "真实盘后出票（不含回放），已成交并卖出的笔，扣 0.3% 成本，每笔平均 / 中位数 / 笔数。没买进、还没卖出的不算。",
};
export const SHORT_REPLAY_COPY = {
  label: "回放已卖出·近12个月",
  tip: "用现行策略在历史上回放重建的推荐，近 12 个月（按信号日）已成交并卖出的笔，扣 0.3% 成本。是研究证据，不是实盘，和左边实盘分开算。",
};

/** 短线：质量画像 + 实盘/回放已卖出，取自 /api/strategies 与 /api/metrics/by-strategy（只读 GET，react-query 共享缓存）。
 * 实盘（live_forward）取全部历史；回放（historical_replay）取近 365 天，与中线画像「近12个月」对齐。两者分开，不混用。 */
export function useShortQuality() {
  const qs = useQuery({ queryKey: ["strategy-quality"], queryFn: () => api.strategies(), staleTime: 300_000 });
  const live = useQuery({ queryKey: ["by-strategy-sold", "live_forward"], queryFn: () => api.metricsByStrategy(undefined, "live_forward"), staleTime: 120_000 });
  const rep = useQuery({ queryKey: ["by-strategy-sold", "historical_replay", 365], queryFn: () => api.metricsByStrategy(365, "historical_replay"), staleTime: 300_000 });
  return (id: string): { quality?: StrategyQuality | null; fullQuality?: ReturnSourceQuality | null; realized: RealizedStats | null; replay: RealizedStats | null } => {
    const q = (qs.data?.find((x) => x.skill_id === id) as { quality?: ReturnSourceQuality | null } | undefined)?.quality ?? null;
    return {
      quality: q as StrategyQuality | null,
      fullQuality: q,
      realized: soldStats(live.data?.find((x) => x.strategy_id === id)),
      replay: soldStats(rep.data?.find((x) => x.strategy_id === id)),
    };
  };
}
export function ShortStrip({ id, style }: { id: string; style?: React.CSSProperties }) {
  const get = useShortQuality();
  const { quality, realized, replay } = get(id);
  return <QualityStrip quality={quality} realized={realized} realizedKind="前瞻" realizedCopy={SHORT_LIVE_COPY}
    replay={{ stats: replay, ...SHORT_REPLAY_COPY }} style={style} />;
}

/** 推荐卡片折叠行用的一行字：稳健/彩票 + 近12个月回测每笔 + 实盘已卖出每笔（满 30 笔才给数，否则写笔数）。 */
export function ShortReturnInline({ id }: { id: string }) {
  const get = useShortQuality();
  const { quality, realized } = get(id);
  const r12 = quality?.R12;
  const rn = realized?.n ?? 0;
  const liveText = rn >= MIN_SAMPLE ? fmt(realized?.avg) : `${rn} 笔，样本不足`;
  return (
    <span className="qinline" title={`回测近12个月：每笔 ${fmt(r12?.avg)}（${r12?.n ?? "—"} 笔，扣 0.3% 成本）\n实盘已卖出：每笔 ${fmt(realized?.avg)}，中位数 ${fmt(realized?.median)}，${rn} 笔（扣成本，不含回放）`}>
      <QualityChip quality={quality} />
      <span className="qinline__k">回测近12月</span><b className={cls(r12?.avg)}>{fmt(r12?.avg)}</b>
      <span className="qinline__k">实盘</span><b className={rn >= MIN_SAMPLE ? cls(realized?.avg) : ""}>{liveText}</b>
    </span>
  );
}
