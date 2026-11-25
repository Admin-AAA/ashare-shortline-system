import { useMemo, useState, type MouseEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  alignBenchmarkPoints, buildEquityCurve, dateAxis, lineSegments,
  nearestIndex, niceTicks, type Classified, type CurveItem,
} from "../utils/equityCurve";

/** 复盘页收益曲线：每日等权累计收益（百分点，按平仓日入账），并叠加沪深300。口径见 utils/equityCurve.ts。
 * 手写 SVG，不引图表库。
 * 2026-11-18 重建：x 轴按真实日期线性定位（不再按点序号等距，长假不再被压成一天）、
 * Y 轴规整刻度 + 零基线、悬浮按日期就近定位、读数统一标注「百分点 pp」，
 * 并明确这是信号累计收益而非账户收益率，避免数值被误读。 */
const W = 960;
const H = 240;
const PAD = { l: 58, r: 16, t: 16, b: 30 };
const HS300_HINT = "沪深300：从曲线首个平仓日的收盘价买入并持有到各平仓日（含持仓标记日），累计涨跌幅，起点归 0%，与策略线共用时间轴。这是区间买入持有，不是「比沪深300多赚」那种按每笔持有期相减。某日没有指数行情时沿用最近一个不晚于该日的收盘价；更早没有行情的日期不画基准点，策略线不截断。策略线起点是首个平仓日的等权收益，不是 0。";
const CALIBER = "口径：同一平仓日已结算的票等权平均后逐日累加（含成本），单位是百分点 pp，不是账户收益率；不同持仓天数与资金占用未加权，因此不能与沪深300 的买入持有涨跌幅直接相减。已确认买入但还没结算的仓位，按「入场价 → 最新价」在标记日以虚线计入浮动（不含卖出费用），不等平仓。实盘前瞻与历史回放合并统计。";

const signed = (v: number, d = 2) => `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
const tone = (v: number) => (v > 0 ? "up" : v < 0 ? "down" : "");

function lastFinite(values: Array<number | null | undefined>): number | null {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const value = values[i];
    if (value != null && Number.isFinite(value)) return value;
  }
  return null;
}

/** classify 不传时按短线推荐记录判定；中线/长线传 classifyHorizon。实盘与回放合在一条线上 */
export function EquityCurve<T = CurveItem>({ items, classify }: { items: T[]; classify?: (it: T) => Classified }) {
  const c = useMemo(() => buildEquityCurve(items, classify), [items, classify]);
  const [hover, setHover] = useState<number | null>(null);
  const pts = c.points;
  const last = pts[pts.length - 1];
  const dates = useMemo(() => pts.map((point) => point.date), [pts]);
  const axis = useMemo(() => dateAxis(dates), [dates]);
  const benchQ = useQuery({
    queryKey: ["hs300-aligned", dates],
    queryFn: () => api.hs300Aligned(dates),
    enabled: dates.length > 0,
  });
  const benchPoints = useMemo(
    () => (benchQ.data ? alignBenchmarkPoints(dates, benchQ.data.points) : null),
    [benchQ.data, dates],
  );
  const benchCum = benchPoints?.map((point) => (point?.cum_pct == null || !Number.isFinite(point.cum_pct) ? null : point.cum_pct)) ?? [];
  const lastBench = lastFinite(benchCum);
  const benchNote = benchQ.isError
    ? "沪深300暂时取不到，策略曲线仍按原口径显示。"
    : benchQ.data && benchQ.data.status !== "ok" && benchQ.data.status !== "empty"
      ? benchQ.data.message
      : null;

  const benchVals = benchCum.filter((value): value is number => value != null);
  const rawLo = Math.min(0, ...pts.map((p) => p.cum), ...benchVals);
  const rawHi = Math.max(0, ...pts.map((p) => p.cum), ...benchVals);
  const ticks = useMemo(() => niceTicks(rawLo, rawHi, 6), [rawLo, rawHi]);
  const lo = Math.min(rawLo, ticks[0] ?? rawLo);
  const hi = Math.max(rawHi, ticks[ticks.length - 1] ?? rawHi);
  const span = hi - lo || 1;
  const plotW = W - PAD.l - PAD.r;
  const x = (i: number) => PAD.l + (axis[i] ?? 0.5) * plotW;
  const y = (v: number) => PAD.t + ((hi - v) / span) * (H - PAD.t - PAD.b);
  // 尾部「浮动」点单独用虚线：已实现收益是实线，持仓浮盈是会回吐的估值，不能画成同一条实线
  const solidEnd = useMemo(() => {
    let i = pts.length - 1;
    while (i >= 0 && pts[i].open > 0) i -= 1;
    return i; // -1 表示整条线都是浮动点（还没有已平仓）
  }, [pts]);
  const toPath = (list: Array<{ i: number; cum: number }>) => list.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.cum).toFixed(1)}`).join("");
  const settled = pts.slice(0, solidEnd + 1).map((p, i) => ({ i, cum: p.cum }));
  const floating = pts.slice(Math.max(solidEnd, 0)).map((p, k) => ({ i: Math.max(solidEnd, 0) + k, cum: p.cum }));
  const path = toPath(settled);
  const floatPath = pts.length > solidEnd + 1 ? toPath(floating) : "";
  const zeroY = y(Math.min(Math.max(0, lo), hi));
  const areaEnd = settled[settled.length - 1];
  const area = settled.length > 1 && areaEnd ? `${path}L${x(areaEnd.i).toFixed(1)},${zeroY.toFixed(1)}L${x(0).toFixed(1)},${zeroY.toFixed(1)}Z` : "";
  const benchPath = lineSegments(pts.map((_, i) => ({ x: x(i), y: benchCum[i] == null ? null : y(benchCum[i] as number) })));
  // X 轴标签按「真实位置」均匀取样，而不是按点序号：日期轴不等距，按序号取可能挤在一起。
  const xLabels = useMemo(() => {
    if (!pts.length) return [];
    const targets = pts.length < 5
      ? Array.from({ length: pts.length }, (_, k) => k / Math.max(1, pts.length - 1))
      : [0, 0.25, 0.5, 0.75, 1];
    const seen = new Set<number>();
    return targets
      .map((t) => nearestIndex(axis, t))
      .filter((i) => (seen.has(i) ? false : (seen.add(i), true)))
      .map((i) => ({ i, date: pts[i].date.slice(0, 10) }));
  }, [pts, axis]);
  const h = hover == null ? null : pts[hover];
  const hb = hover == null ? null : benchPoints?.[hover] ?? null;
  const benchLabel = benchQ.isLoading
    ? "…"
    : lastBench == null
      ? (benchQ.isError || benchQ.data?.status === "unavailable" ? "无数据" : "—")
      : signed(lastBench, 1);

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    if (!pts.length) return;
    const r = e.currentTarget.getBoundingClientRect();
    const vx = ((e.clientX - r.left) / r.width) * W;
    const ratio = Math.max(0, Math.min(1, (vx - PAD.l) / plotW));
    setHover(nearestIndex(axis, ratio));
  };

  return (
    <div className="card eq">
      <div className="eq__hd">
        <b>收益曲线</b>
        {solidEnd >= 0 && <span className="eq__key" title="同一平仓日已结算的票等权平均后逐日累加"><i className="eq__swatch" />策略累计</span>}
        {c.openCount > 0 && <span className="eq__key" title="已确认买入但还没结算，按「入场价 → 最新价」计的浮动收益，虚线"><i className="eq__swatch eq__swatch--float" />持仓浮动</span>}
        <span className="eq__key" title={HS300_HINT}><i className="eq__swatch eq__swatch--hs300" />沪深300</span>
        <span className="small muted">按真实日期定位 · 单位 pp</span>
        <span style={{ marginLeft: "auto" }} className="small muted">策略累计</span>
        <b className={`num ${last ? tone(last.cum) : ""}`}>{last ? signed(last.cum, 1) : "—"}<span className="eq__unit"> pp</span></b>
        <span className="small muted">最大回撤</span>
        <b className="num down">{pts.length ? c.maxDrawdown.toFixed(1) : "—"}<span className="eq__unit"> pp</span></b>
        <span className="small muted">沪深300</span>
        <b className={`num ${lastBench == null ? "" : tone(lastBench)}`}>{pts.length ? benchLabel : "—"}<span className="eq__unit">%</span></b>
      </div>
      <div className="eq__note eq__note--caliber small muted">{CALIBER}</div>
      {benchNote && <div className="eq__note small muted">{benchNote}</div>}
      {pts.length ? (
        <svg className="eq__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`收益曲线：${pts[0].date} 至 ${last!.date}，${pts.length} 个入账日${c.openCount ? `（含 ${c.openCount} 笔持仓浮动）` : ""}，策略累计 ${signed(last!.cum, 1)} 个百分点${lastBench == null ? "" : `，沪深300 ${signed(lastBench, 1)}%`}，最大回撤 ${c.maxDrawdown.toFixed(1)} 个百分点`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className={v === 0 ? "eq__zero" : "eq__grid"} />
              <text x={PAD.l - 8} y={y(v) + 4} textAnchor="end" className="eq__tick">{signed(v, 0)}</text>
            </g>
          ))}
          {xLabels.map(({ i, date }) => (
            <text key={date} x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"} className="eq__xlab">{date}</text>
          ))}
          {area && <path d={area} className="eq__area" />}
          {path && <path d={path} className="eq__line" />}
          {floatPath && <path d={floatPath} className="eq__line eq__line--float" />}
          {benchPath.map((d, i) => <path key={i} d={d} className="eq__line eq__line--hs300" />)}
          {last && <circle cx={x(pts.length - 1)} cy={y(last.cum)} r={last.open > 0 ? 4 : 3} className={last.open > 0 ? "eq__dot eq__dot--float" : "eq__dot"} />}
          {h && (
            <g>
              <line x1={x(hover!)} x2={x(hover!)} y1={PAD.t} y2={H - PAD.b} className="eq__cross" />
              <circle cx={x(hover!)} cy={y(h.cum)} r={4} className={h.open > 0 ? "eq__dot eq__dot--float" : "eq__dot"} />
              {hb?.cum_pct != null && <circle cx={x(hover!)} cy={y(hb.cum_pct)} r={3.5} className="eq__dot eq__dot--hs300" />}
            </g>
          )}
        </svg>
      ) : (
        <div className="eq__empty muted small">暂无已结算或已确认买入的成交，曲线待第一笔成交后出现</div>
      )}
      <div className="eq__ft small muted">
        {h ? (
          <span>
            <b className="num">{h.date}</b>
            {h.n > 0
              ? <> 平仓 {h.n} 笔 · 当日均值 <span className={`num ${tone(h.dayRet)}`}>{signed(h.dayRet)} pp</span></>
              : <span className="muted"> 持仓标记日 · 尚无平仓</span>}
            {h.open > 0 && <> · 浮动 {h.open} 笔 <span className={`num ${tone(h.float)}`}>{signed(h.float)} pp</span>（未结算）</>}
            {" · "}策略累计 <b className={`num ${tone(h.cum)}`}>{signed(h.cum)} pp</b>
            {hb?.cum_pct != null && <> · 沪深300 <b className={`num ${tone(hb.cum_pct)}`}>{signed(hb.cum_pct)}%</b>{hb.filled && hb.index_date ? `（沿用 ${hb.index_date}）` : ""}{hb.stale ? " · 行情间隔较长" : ""}</>}
            {hb && hb.cum_pct == null && <> · 沪深300 当日无对齐行情</>}
          </span>
        ) : (
          <span>{c.trades} 笔已结算，{pts.length} 个入账日{c.openCount > 0 ? `（含 ${c.openCount} 笔持仓浮动）` : ""}{pts.length ? ` · ${pts[0].date} 至 ${pts[pts.length - 1].date}` : ""}</span>
        )}
        <span style={{ marginLeft: "auto" }}>未入曲线：持有中 {c.holding} · 取消/未触发/过期/待确认 {c.excluded}</span>
      </div>
    </div>
  );
}
