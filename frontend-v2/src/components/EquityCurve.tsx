import { useMemo, useState, type MouseEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { alignBenchmarkPoints, buildEquityCurve, lineSegments, type Classified, type CurveItem } from "../utils/equityCurve";

/** 复盘页收益曲线：每日等权累计收益（百分点，按平仓日入账），并叠加沪深300。口径见 utils/equityCurve.ts。
 * 手写 SVG，不引图表库：两条折线 + 悬浮读数。 */
const W = 960;
const H = 200;
const PAD = { l: 48, r: 12, t: 12, b: 24 };
const HS300_HINT = "沪深300：从曲线首个平仓日的收盘价买入并持有到各平仓日，累计涨跌幅，起点归 0%，与策略线共用时间轴。这是区间买入持有，不是「比沪深300多赚」那种按每笔持有期相减。某日没有指数行情时沿用最近一个不晚于该日的收盘价；更早没有行情的日期不画基准点，策略线不截断。策略线起点是首个平仓日的等权收益，不是 0。";

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

  const vals = [0, ...pts.map((p) => p.cum), ...benchCum.filter((value): value is number => value != null)];
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (i: number) => PAD.l + (pts.length > 1 ? (i / (pts.length - 1)) * (W - PAD.l - PAD.r) : (W - PAD.l - PAD.r) / 2);
  const y = (v: number) => PAD.t + ((hi - v) / span) * (H - PAD.t - PAD.b);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join("");
  const benchPath = lineSegments(pts.map((_, i) => ({ x: x(i), y: benchCum[i] == null ? null : y(benchCum[i] as number) })));
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
    const i = pts.length > 1 ? Math.round(((vx - PAD.l) / (W - PAD.l - PAD.r)) * (pts.length - 1)) : 0;
    setHover(Math.max(0, Math.min(pts.length - 1, i)));
  };

  return (
    <div className="card eq">
      <div className="eq__hd">
        <b>收益曲线</b>
        <span className="eq__key" title="同一平仓日已结算的票等权平均后逐日累加"><i className="eq__swatch" />策略</span>
        <span className="eq__key" title={HS300_HINT}><i className="eq__swatch eq__swatch--hs300" />沪深300</span>
        <span className="small muted">每日等权累加（含成本，百分点；实盘与回放合计）。沪深300为同一时间轴上的买入持有累计涨跌幅，首日归 0%。</span>
        <span style={{ marginLeft: "auto" }} className="small muted">策略累计</span>
        <b className={`num ${last ? tone(last.cum) : ""}`}>{last ? signed(last.cum, 1) : "—"}</b>
        <span className="small muted">沪深300</span>
        <b className={`num ${lastBench == null ? "" : tone(lastBench)}`}>{pts.length ? benchLabel : "—"}</b>
        <span className="small muted">最大回撤</span>
        <b className="num down">{pts.length ? c.maxDrawdown.toFixed(1) : "—"}</b>
      </div>
      {benchNote && <div className="eq__note small muted">{benchNote}</div>}
      {pts.length ? (
        <svg className="eq__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`收益曲线：${pts[0].date} 至 ${last!.date}，${pts.length} 个平仓日，策略累计 ${signed(last!.cum, 1)} 个百分点${lastBench == null ? "" : `，沪深300 ${signed(lastBench, 1)}%`}，最大回撤 ${c.maxDrawdown.toFixed(1)}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {[0, hi, lo].filter((v, i, a) => a.slice(0, i).every((u) => Math.abs(y(u) - y(v)) >= 14)).map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className={v === 0 ? "eq__base" : "eq__grid"} />
              <text x={PAD.l - 6} y={y(v) + 4} textAnchor="end" className="eq__tick">{signed(v, 0)}</text>
            </g>
          ))}
          <text x={PAD.l} y={H - 6} className="eq__tick">{pts[0].date}</text>
          <text x={W - PAD.r} y={H - 6} textAnchor="end" className="eq__tick">{last!.date}</text>
          <path d={path} className="eq__line" />
          {benchPath.map((d, i) => <path key={i} d={d} className="eq__line eq__line--hs300" />)}
          {h && (
            <g>
              <line x1={x(hover!)} x2={x(hover!)} y1={PAD.t} y2={H - PAD.b} className="eq__grid" />
              <circle cx={x(hover!)} cy={y(h.cum)} r={4} className="eq__dot" />
              {hb?.cum_pct != null && <circle cx={x(hover!)} cy={y(hb.cum_pct)} r={3.5} className="eq__dot eq__dot--hs300" />}
            </g>
          )}
        </svg>
      ) : (
        <div className="eq__empty muted small">暂无已结算的成交，曲线待第一笔平仓后出现</div>
      )}
      <div className="eq__ft small muted">
        {h ? (
          <span>
            <b className="num">{h.date}</b> 平仓 {h.n} 笔 · 当日均值 <span className={`num ${tone(h.dayRet)}`}>{signed(h.dayRet)}%</span> · 策略累计 <b className={`num ${tone(h.cum)}`}>{signed(h.cum)}</b>
            {hb?.cum_pct != null && <> · 沪深300 <b className={`num ${tone(hb.cum_pct)}`}>{signed(hb.cum_pct)}</b>{hb.filled && hb.index_date ? `（沿用 ${hb.index_date}）` : ""}{hb.stale ? " · 行情间隔较长" : ""}</>}
            {hb && hb.cum_pct == null && <> · 沪深300 当日无对齐行情</>}
          </span>
        ) : (
          <span>{c.trades} 笔已结算，{pts.length} 个平仓日</span>
        )}
        <span style={{ marginLeft: "auto" }}>未入曲线：持有中 {c.holding} · 取消/未触发/过期/待确认 {c.excluded}</span>
      </div>
    </div>
  );
}
