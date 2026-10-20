import { useMemo, useState, type MouseEvent } from "react";
import { buildEquityCurve, type Classified, type CurveItem } from "../utils/equityCurve";

/** 复盘页收益曲线：每日等权累计收益（百分点，按平仓日入账）。口径见 utils/equityCurve.ts。
 * 手写 SVG，不引图表库：只有一条折线 + 悬浮读数，库的体积和主题适配成本都不值。 */
const W = 960;
const H = 200;
const PAD = { l: 48, r: 12, t: 12, b: 24 };

const signed = (v: number, d = 2) => `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
const tone = (v: number) => (v > 0 ? "up" : v < 0 ? "down" : "");

/** classify 不传时按短线推荐记录判定；中线/长线传 classifyHorizon。实盘与回放合在一条线上 */
export function EquityCurve<T = CurveItem>({ items, classify }: { items: T[]; classify?: (it: T) => Classified }) {
  const c = useMemo(() => buildEquityCurve(items, classify), [items, classify]);
  const [hover, setHover] = useState<number | null>(null);
  const pts = c.points;
  const last = pts[pts.length - 1];

  const vals = [0, ...pts.map((p) => p.cum)];
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (i: number) => PAD.l + (pts.length > 1 ? (i / (pts.length - 1)) * (W - PAD.l - PAD.r) : (W - PAD.l - PAD.r) / 2);
  const y = (v: number) => PAD.t + ((hi - v) / span) * (H - PAD.t - PAD.b);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join("");
  const h = hover == null ? null : pts[hover];

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
        <span className="small muted">每日等权：同一平仓日已结算的票取平均计入当天，逐日累加（含成本，单位：百分点；实盘与回放合计）</span>
        <span style={{ marginLeft: "auto" }} className="small muted">累计</span>
        <b className={`num ${last ? tone(last.cum) : ""}`}>{last ? signed(last.cum, 1) : "—"}</b>
        <span className="small muted">最大回撤</span>
        <b className="num down">{pts.length ? c.maxDrawdown.toFixed(1) : "—"}</b>
        <span className="small muted" title="每笔固定金额简单相加；当天票越多权重越大，只作对照">逐笔累加</span>
        <b className={`num ${tone(c.sumPct)}`}>{pts.length ? `${signed(c.sumPct, 1)} 个百分点` : "—"}</b>
      </div>
      {pts.length ? (
        <svg className="eq__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`收益曲线：${pts[0].date} 至 ${last!.date}，${pts.length} 个平仓日，累计 ${signed(last!.cum, 1)} 个百分点，最大回撤 ${c.maxDrawdown.toFixed(1)}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {[0, hi, lo].filter((v, i, a) => a.slice(0, i).every((u) => Math.abs(y(u) - y(v)) >= 14)).map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className={v === 0 ? "eq__base" : "eq__grid"} />
              <text x={PAD.l - 6} y={y(v) + 4} textAnchor="end" className="eq__tick">{signed(v, 0)}</text>
            </g>
          ))}
          <text x={PAD.l} y={H - 6} className="eq__tick">{pts[0].date}</text>
          <text x={W - PAD.r} y={H - 6} textAnchor="end" className="eq__tick">{last!.date}</text>
          <path d={path} className="eq__line" />
          {h && (
            <g>
              <line x1={x(hover!)} x2={x(hover!)} y1={PAD.t} y2={H - PAD.b} className="eq__grid" />
              <circle cx={x(hover!)} cy={y(h.cum)} r={4} className="eq__dot" />
            </g>
          )}
        </svg>
      ) : (
        <div className="eq__empty muted small">暂无已结算的成交，曲线待第一笔平仓后出现</div>
      )}
      <div className="eq__ft small muted">
        {h ? (
          <span><b className="num">{h.date}</b> 平仓 {h.n} 笔 · 当日均值 <span className={`num ${tone(h.dayRet)}`}>{signed(h.dayRet)}%</span> · 累计 <b className={`num ${tone(h.cum)}`}>{signed(h.cum)}</b></span>
        ) : (
          <span>{c.trades} 笔已结算，{pts.length} 个平仓日</span>
        )}
        <span style={{ marginLeft: "auto" }}>未入曲线：持有中 {c.holding} · 取消/未触发/过期/待确认 {c.excluded}</span>
      </div>
    </div>
  );
}
