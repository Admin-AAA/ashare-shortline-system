import "../styles/quality.css";

/** 后端 quality 块（短线 GET /api/strategies、中长线 GET /api/horizons/{h}/strategies 的 metrics.quality）。 */
export type QualityWindow = { n?: number; avg?: number; median?: number; trim_both5?: number; pf?: number; mdd?: number; label?: string };
export type StrategyQuality = { label?: string; label_rule?: string; R12?: QualityWindow; N3?: QualityWindow };

/** 前瞻/回放已结算统计（短线 /api/metrics/by-strategy；中长线 /api/horizons/{h}/metrics by_strategy） */
export type RealizedStats = { n?: number | null; avg?: number | null; median?: number | null };

export const MIN_SAMPLE = 30;
const FALLBACK_RULE = "近12个月与 2024-01 至今两段都满足「两头各去5%后均值>0 且 中位数>0」为稳健型，否则为彩票型。";

const fmt = (v?: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const cls = (v?: number | null) => (v == null ? "" : v > 0 ? "qt__up" : v < 0 ? "qt__down" : "");

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

export type QualityRow = { id: string; name: string; quality?: StrategyQuality | null; realized?: RealizedStats | null };

function Realized({ r }: { r?: RealizedStats | null }) {
  if (!r || r.n == null || r.n === 0) return <><td className="sep" colSpan={2}><span className="qt__low">暂无</span></td><td>0</td></>;
  if (r.n < MIN_SAMPLE)
    return <><td className="sep qt__low" colSpan={2} title={`n=${r.n} < ${MIN_SAMPLE}：平均 ${fmt(r.avg)}，中位数 ${fmt(r.median)}（样本太少，仅供参考）`}>样本不足</td><td>{r.n}</td></>;
  return <><td className={`sep ${cls(r.avg)}`}>{fmt(r.avg)}</td><td className={cls(r.median)}>{fmt(r.median)}</td><td>{r.n}</td></>;
}

/** 各策略每笔收益：回测（近12个月 / 2024-01至今）+ 已结算（前瞻或回放）+ 标签。 */
export function QualityTable({ title, rows, realizedTitle, sub, notes }: { title: string; rows: QualityRow[]; realizedTitle: string; sub?: string; notes?: React.ReactNode[] }) {
  const rule = rows.find((r) => r.quality?.label_rule)?.quality?.label_rule ?? FALLBACK_RULE;
  return (
    <section className="qt">
      <div className="qt__head">
        <b>{title}</b>
        <span className="qt__sub">{sub ?? "每笔收益均已扣 0.3% 成本；标签来自回测，规则："}{!sub && rule}</span>
      </div>
      <div className="qt__wrap">
        <table>
          <thead>
            <tr>
              <th className="l" rowSpan={2}>策略</th>
              <th className="grp" colSpan={3}>回测 · 近12个月</th>
              <th className="grp" colSpan={4}>回测 · 2024-01 至今（约2.8年）</th>
              <th className="grp" colSpan={3}>{realizedTitle}</th>
              <th rowSpan={2} className="sep" title={rule}>标签</th>
            </tr>
            <tr>
              <th>平均/笔</th><th>中位数</th><th>笔数</th>
              <th className="sep">平均/笔</th><th>中位数</th><th title="两头各去 5% 后的平均">去极值均值</th><th>笔数</th>
              <th className="sep">平均/笔</th><th>中位数</th><th>笔数</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const a = r.quality?.R12, b = r.quality?.N3;
              return (
                <tr key={r.id}>
                  <td className="l">{r.name}</td>
                  <td className={cls(a?.avg)}>{fmt(a?.avg)}</td><td className={cls(a?.median)}>{fmt(a?.median)}</td><td>{a?.n ?? "—"}</td>
                  <td className={`sep ${cls(b?.avg)}`}>{fmt(b?.avg)}</td><td className={cls(b?.median)}>{fmt(b?.median)}</td><td className={cls(b?.trim_both5)}>{fmt(b?.trim_both5)}</td><td>{b?.n ?? "—"}</td>
                  <Realized r={r.realized} />
                  <td className="sep"><QualityChip quality={r.quality} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {notes && notes.length > 0 && <ul className="qt__notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </section>
  );
}
