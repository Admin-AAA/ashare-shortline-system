import { useMemo, useState, type MouseEvent } from "react";
import type { HorizonOverlapPortfolios, OverlapPerTrade, OverlapScheme } from "../api/client";
import { niceTicks } from "../utils/equityCurve";
import { indexTicks, pairMonths, spreadLabels } from "../utils/overlapPortfolio";

/** 中线诊断页「组合视图」：同一信号日同一只票被多个策略推时，按两种买法算账户层面的收益、回撤和敞口。
 * 数据是 /api/horizons/mid/metrics 的 overlap_portfolios（backend/app/services/overlap_portfolio.py），只做度量，
 * 不改任何策略的出票和结算。颜色按主题各取一组（horizon.css 的 .hz-ov），已过 dataviz 配色校验。 */

const pct = (v?: number | null, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const pp = (v?: number | null, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}pp`);
const share = (v?: number | null, d = 1) => (v == null ? "—" : `${v.toFixed(d)}%`);
const num = (v?: number | null, d = 1) => (v == null ? "—" : v.toFixed(d));
const pnl = (v?: number | null) => (v == null ? "" : v > 0 ? "hz-up" : v < 0 ? "hz-down" : "");

type Scheme = "A" | "B";
const SCHEME_LABEL: Record<Scheme, string> = { A: "A 跟全部策略买", B: "B 去重每票一份" };
const SCHEME_TIP: Record<Scheme, string> = {
  A: "每笔出票一份：同一信号日同一只票被 k 个策略推，就买 k 份。这是诊断页现有逐笔口径的资金版。",
  B: "同一信号日同一只票只买一份；k 个策略同推时，这一份平分给这 k 笔，各按自己的离场走。",
};
const STATUS: Record<string, [string, string]> = {
  pending: ["待买入", "amber"], holding: ["持有中", "blue"], closed: ["已平仓", "gray"], skipped: ["开盘涨停·已取消", "gray"],
};
const HEAD_TIP = {
  net: "扣 0.07% 往返税费后，按交易日复利的全期收益",
  bench: "每笔换成同一持有窗口（入场日之后到卖出日）的全市场等权组合 EWALL.A，按同样仓位和排期组合，扣同样成本",
  excess: "组合 − 同排期 EWALL.A，单位百分点",
  invested: "滚动期内已投入资金占全部资金的比例（空槽与批内已卖出部分是现金）",
  positions: "滚动期内平均在手份数。A：策略出票笔数；B：去重后的（信号日，代码）份数",
  stocks: "滚动期内平均持有的不同股票数；同一只票在不同信号日的仓位合并算一只",
  effn: "有效持仓数 = (Σw)² / Σw²，越大越分散",
  maxw: "滚动期内单只股票占全部资金的最大比例；悬停看是哪只票、哪天",
  perTrade: "不复利、不看资金占用：A 每笔一份（即「平均/笔」换成 0.07% 成本），B 每个（信号日，代码）一份",
};

const W = 960;
const H = 260;
const PAD = { l: 58, r: 118, t: 16, b: 30 };
const SERIES = [
  { key: "A", cls: "a", label: "A 跟全部策略买", short: "A" },
  { key: "B", cls: "b", label: "B 去重每票一份", short: "B" },
  { key: "bench", cls: "bench", label: "同排期 EWALL.A（按 A 的仓位）", short: "EWALL.A" },
] as const;

function OverlapCurve({ c }: { c: HorizonOverlapPortfolios["curves"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const n = c.dates.length;
  const { lo, hi, ticks } = useMemo(() => {
    let rawLo = 0;
    let rawHi = 0;
    for (const s of SERIES) for (const v of c[s.key]) { if (v < rawLo) rawLo = v; if (v > rawHi) rawHi = v; }
    const t = niceTicks(rawLo, rawHi, 6);
    return { lo: Math.min(rawLo, t[0] ?? rawLo), hi: Math.max(rawHi, t[t.length - 1] ?? rawHi), ticks: t };
  }, [c]);
  if (!n) return null;
  const span = hi - lo || 1;
  const plotW = W - PAD.l - PAD.r;
  const x = (i: number) => PAD.l + (n > 1 ? i / (n - 1) : 0.5) * plotW;
  const y = (v: number) => PAD.t + ((hi - v) / span) * (H - PAD.t - PAD.b);
  const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const ends = SERIES.map((s) => c[s.key][n - 1]);
  const endY = ends.map(y);
  const labelY = spreadLabels(endY, 15, PAD.t + 4, H - PAD.b - 4);
  const xEnd = x(n - 1);
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (((e.clientX - r.left) / r.width) * W - PAD.l) / plotW));
    setHover(Math.round(ratio * (n - 1)));
  };
  const h = hover;
  return (
    <div className="hz-ov__chart">
      <div className="hz-ov__legend">
        {SERIES.map((s) => <span key={s.key} className="hz-ov__key"><i className={`hz-ov__swatch hz-ov__swatch--${s.cls}`} />{s.label}</span>)}
        <span className="hz-muted">累计收益（%），按交易日复利；横轴从第一笔入场后到最后一笔卖出，按交易日等距</span>
      </div>
      <svg className="eq__svg" viewBox={`0 0 ${W} ${H}`} role="img" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
        aria-label={`组合累计收益 ${c.dates[0]} 至 ${c.dates[n - 1]}：A ${pct(ends[0])}，B ${pct(ends[1])}，同排期 EWALL.A ${pct(ends[2])}`}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className={v === 0 ? "eq__zero" : "eq__grid"} />
            <text x={PAD.l - 8} y={y(v) + 4} textAnchor="end" className="eq__tick">{`${v > 0 ? "+" : ""}${v}%`}</text>
          </g>
        ))}
        {indexTicks(n, 5).map((i) => (
          <text key={i} x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} className="eq__xlab">{c.dates[i]}</text>
        ))}
        {[...SERIES].reverse().map((s) => <path key={s.key} d={path(c[s.key])} className={`hz-ov__line hz-ov__line--${s.cls}`} />)}
        {SERIES.map((s, k) => (
          <g key={s.key}>
            {Math.abs(labelY[k] - endY[k]) > 2 && <line x1={xEnd + 4} y1={endY[k]} x2={xEnd + 12} y2={labelY[k]} className="hz-ov__leader" />}
            <circle cx={xEnd} cy={endY[k]} r={3.5} className={`hz-ov__dot hz-ov__dot--${s.cls}`} />
            <text x={xEnd + 14} y={labelY[k] + 4} className="hz-ov__endlab">{s.short} {pct(ends[k], 1)}</text>
          </g>
        ))}
        {h != null && (
          <g>
            <line x1={x(h)} x2={x(h)} y1={PAD.t} y2={H - PAD.b} className="eq__cross" />
            {SERIES.map((s) => <circle key={s.key} cx={x(h)} cy={y(c[s.key][h])} r={4} className={`hz-ov__dot hz-ov__dot--${s.cls}`} />)}
          </g>
        )}
      </svg>
      <div className="eq__ft small muted">
        {h != null ? (
          <span>
            <b className="num">{c.dates[h]}</b>
            {" · "}A <b className={`num ${pnl(c.A[h])}`}>{pct(c.A[h])}</b>
            {" · "}B <b className={`num ${pnl(c.B[h])}`}>{pct(c.B[h])}</b>
            {" · "}同排期 EWALL.A <b className={`num ${pnl(c.bench[h])}`}>{pct(c.bench[h])}</b>
            {" · "}A − B <b className={`num ${pnl(c.A[h] - c.B[h])}`}>{pp(c.A[h] - c.B[h])}</b>
          </span>
        ) : (
          <span>{c.dates[0]} ~ {c.dates[n - 1]}，{n} 个交易日；悬停看逐日读数</span>
        )}
      </div>
    </div>
  );
}

function SchemeRow({ k, s, per }: { k: Scheme; s: OverlapScheme; per: OverlapPerTrade }) {
  const maxTip = s.max_single_weight_code
    ? `${s.max_single_weight_code} · ${s.max_single_weight_date}；滚动期内每天最大单票权重平均 ${share(s.avg_daily_max_weight, 2)}`
    : undefined;
  return (
    <tr>
      <td title={SCHEME_TIP[k]}><i className={`hz-ov__swatch hz-ov__swatch--${k.toLowerCase()}`} /> {SCHEME_LABEL[k]}</td>
      <td className={`r hz-num ${pnl(s.net_ret)}`}>{pct(s.net_ret)}</td>
      <td className={`r hz-num ${pnl(s.bench_ret)}`}>{pct(s.bench_ret)}</td>
      <td className={`r hz-num ${pnl(s.excess_ret)}`}>{pp(s.excess_ret)}</td>
      <td className={`r hz-num ${pnl(s.max_drawdown)}`}>{pct(s.max_drawdown)}</td>
      <td className={`r hz-num ${pnl(s.bench_max_drawdown)}`}>{pct(s.bench_max_drawdown)}</td>
      <td className="r hz-num">{share(s.avg_invested_pct)}</td>
      <td className="r hz-num">{num(s.avg_positions)}</td>
      <td className="r hz-num">{num(s.avg_stocks)}</td>
      <td className="r hz-num">{num(s.avg_effective_n)}</td>
      <td className="r hz-num" title={maxTip}>{share(s.max_single_weight, 2)}</td>
      <td className={`r hz-num ${pnl(per.net_ret)}`}>{pct(per.net_ret)}</td>
      <td className={`r hz-num ${pnl(per.excess_ret)}`}>{pp(per.excess_ret)}</td>
    </tr>
  );
}

const diff = (a?: number | null, b?: number | null) => (a == null || b == null ? null : Number((a - b).toFixed(2)));

export function OverlapPortfolioSection({ d }: { d: HorizonOverlapPortfolios }) {
  const months = useMemo(() => pairMonths(d.A.by_month, d.B.by_month), [d]);
  const rw = d.rolling_window;
  const note = `口径：资金分 ${d.slots} 份（最长设计持有期），每个信号日用 1 份买入当天出票，持有期走完收回再用；没有信号的交易日和批内已卖出的部分按现金计，按交易日复利，一直走到最后一笔卖出。每笔收益在自己的持有窗口（入场日之后到卖出日）几何均摊成日收益。成本统一按 ${d.cost_pct}% 往返税费（上面的核心 KPI 和按策略表用 0.3%）。平均仓位、在手份数、持有票数、有效持仓数、单票最大权重只统计滚动期 ${rw.start} ~ ${rw.end}（${rw.days} 个交易日），${rw.ramped ? "去掉首尾建仓和清仓爬坡" : `当前筛选的信号日跨度不足 ${d.slots} 个交易日，滚动期从第一个持有日算起、含建仓爬坡，仓位类数字偏低`}；单票权重按占全部资金算。逐月按持仓所在的交易日归月，不是信号月，各月连乘等于全期。`;
  return (
    <section className="panel hz-pad hz-ov">
      <div className="hz-sechead"><b>组合视图 · 多策略同推的票怎么买</b><span className="hz-muted">
        只做度量，不改出票和结算；随上方来源 / 窗口筛选。{d.eligible_picks} 笔已平仓出票 = {d.eligible_signals} 个去重信号（信号日 × 代码），{d.signal_days} 个信号日
        {d.excluded_closed > 0 ? `；另有 ${d.excluded_closed} 笔已平仓的日期不在 EWALL.A 交易日历内，未计入` : ""}
      </span></div>
      <div className="hz-tablewrap"><table className="hz-table">
        <thead><tr>
          <th>买法</th><th className="r" title={HEAD_TIP.net}>全期净收益</th><th className="r" title={HEAD_TIP.bench}>同排期 EWALL.A</th>
          <th className="r" title={HEAD_TIP.excess}>超额</th><th className="r">最大回撤</th><th className="r">EWALL.A 回撤</th>
          <th className="r" title={HEAD_TIP.invested}>平均仓位</th><th className="r" title={HEAD_TIP.positions}>在手份数</th>
          <th className="r" title={HEAD_TIP.stocks}>持有票数</th><th className="r" title={HEAD_TIP.effn}>有效持仓数</th>
          <th className="r" title={HEAD_TIP.maxw}>单票最大权重</th><th className="r" title={HEAD_TIP.perTrade}>逐笔平均</th>
          <th className="r" title={HEAD_TIP.perTrade}>逐笔超额</th>
        </tr></thead>
        <tbody>
          <SchemeRow k="A" s={d.A} per={d.per_trade.all} />
          <SchemeRow k="B" s={d.B} per={d.per_trade.dedup} />
          <tr className="hz-ov__diff">
            <td>A − B</td>
            <td className={`r hz-num ${pnl(diff(d.A.net_ret, d.B.net_ret))}`}>{pp(diff(d.A.net_ret, d.B.net_ret))}</td>
            <td />
            <td className={`r hz-num ${pnl(diff(d.A.excess_ret, d.B.excess_ret))}`}>{pp(diff(d.A.excess_ret, d.B.excess_ret))}</td>
            <td className={`r hz-num ${pnl(diff(d.A.max_drawdown, d.B.max_drawdown))}`}>{pp(diff(d.A.max_drawdown, d.B.max_drawdown))}</td>
            <td colSpan={6} />
            <td className={`r hz-num ${pnl(diff(d.per_trade.all.net_ret, d.per_trade.dedup.net_ret))}`}>{pp(diff(d.per_trade.all.net_ret, d.per_trade.dedup.net_ret))}</td>
            <td className={`r hz-num ${pnl(diff(d.per_trade.all.excess_ret, d.per_trade.dedup.excess_ret))}`}>{pp(diff(d.per_trade.all.excess_ret, d.per_trade.dedup.excess_ret))}</td>
          </tr>
        </tbody>
      </table></div>

      <OverlapCurve c={d.curves} />

      <div className="hz-ov__sub">逐月对比<span className="hz-muted">A 净收益高于 B 的月份 {months.aWins} / {months.compared}；回撤为月内最大回撤</span></div>
      <div className="hz-tablewrap"><table className="hz-table">
        <thead><tr>
          <th>月份</th><th className="r">交易日</th><th className="r">A 净收益</th><th className="r">A 超额</th><th className="r">A 回撤</th>
          <th className="r">B 净收益</th><th className="r">B 超额</th><th className="r">B 回撤</th>
          <th className="r" title="按 A 的仓位和排期；B 的超额已按 B 自己的同排期基准算">同排期 EWALL.A</th><th className="r">A − B</th>
        </tr></thead>
        <tbody>
          {months.rows.map((r) => (
            <tr key={r.month}>
              <td className="hz-num">{r.month}</td><td className="r hz-num">{r.days}</td>
              <td className={`r hz-num ${pnl(r.a.net_ret)}`}>{pct(r.a.net_ret)}</td>
              <td className={`r hz-num ${pnl(r.a.excess_ret)}`}>{pp(r.a.excess_ret)}</td>
              <td className={`r hz-num ${pnl(r.a.max_drawdown)}`}>{pct(r.a.max_drawdown)}</td>
              <td className={`r hz-num ${pnl(r.b?.net_ret)}`}>{pct(r.b?.net_ret)}</td>
              <td className={`r hz-num ${pnl(r.b?.excess_ret)}`}>{pp(r.b?.excess_ret)}</td>
              <td className={`r hz-num ${pnl(r.b?.max_drawdown)}`}>{pct(r.b?.max_drawdown)}</td>
              <td className={`r hz-num ${pnl(r.a.bench_ret)}`}>{pct(r.a.bench_ret)}</td>
              <td className={`r hz-num ${pnl(r.diff)}`}>{pp(r.diff)}</td>
            </tr>
          ))}
          <tr className="hz-ov__total">
            <td>全期</td><td className="r hz-num">{d.curves.dates.length}</td>
            <td className={`r hz-num ${pnl(d.A.net_ret)}`}>{pct(d.A.net_ret)}</td>
            <td className={`r hz-num ${pnl(d.A.excess_ret)}`}>{pp(d.A.excess_ret)}</td>
            <td className={`r hz-num ${pnl(d.A.max_drawdown)}`}>{pct(d.A.max_drawdown)}</td>
            <td className={`r hz-num ${pnl(d.B.net_ret)}`}>{pct(d.B.net_ret)}</td>
            <td className={`r hz-num ${pnl(d.B.excess_ret)}`}>{pp(d.B.excess_ret)}</td>
            <td className={`r hz-num ${pnl(d.B.max_drawdown)}`}>{pct(d.B.max_drawdown)}</td>
            <td className={`r hz-num ${pnl(d.A.bench_ret)}`}>{pct(d.A.bench_ret)}</td>
            <td className={`r hz-num ${pnl(diff(d.A.net_ret, d.B.net_ret))}`}>{pp(diff(d.A.net_ret, d.B.net_ret))}</td>
          </tr>
        </tbody>
      </table></div>

      <div className="hz-ov__sub">出票层去重明细<span className="hz-muted">最近 {d.recent_signal_days} 个信号日；按出票算，不看后来是否买到。A 占当日 = 同推笔数 / 当日出票，B 占当日 = 1 / 去重后票数</span></div>
      <div className="hz-tablewrap hz-ov__scroll"><table className="hz-table">
        <thead><tr>
          <th>信号日</th><th className="r">出票</th><th className="r">去重后</th><th>多策略同推的票</th><th className="r">同推数</th>
          <th>推送策略</th><th className="r">A 占当日</th><th className="r">B 占当日</th><th>状态</th>
        </tr></thead>
        <tbody>{d.recent_days.flatMap((day) => day.shared.length ? day.shared.map((s, i) => {
          const [label, tone] = STATUS[s.status] ?? [s.status, "gray"];
          return (
            <tr key={`${day.signal_date}-${s.code}`}>
              <td className="hz-num">{i === 0 ? day.signal_date : ""}</td>
              <td className="r hz-num">{i === 0 ? day.picks : ""}</td>
              <td className="r hz-num">{i === 0 ? day.stocks : ""}</td>
              <td><span className="hz-num">{s.code}</span> {s.name ?? ""}</td>
              <td className="r hz-num">{s.overlap}</td>
              <td className="hz-ov__wrap">{s.strategies.join("、")}</td>
              <td className="r hz-num">{share(s.weight_a_pct)}</td>
              <td className="r hz-num">{share(s.weight_b_pct)}</td>
              <td><span className={`hz-pill hz-pill--${tone}`}>{label}</span></td>
            </tr>
          );
        }) : [
          <tr key={day.signal_date}>
            <td className="hz-num">{day.signal_date}</td><td className="r hz-num">{day.picks}</td><td className="r hz-num">{day.stocks}</td>
            <td colSpan={6} className="hz-muted">当天没有被 2 个及以上策略同推的票</td>
          </tr>,
        ])}</tbody>
      </table></div>
      <div className="hz-ov__note">{note}</div>
    </section>
  );
}
