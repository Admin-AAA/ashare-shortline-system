import { useEffect, useState } from "react";
import { X, Play, AlertTriangle } from "lucide-react";
import { api, type HorizonBar, type HorizonPick, type HorizonPickDetail, type HorizonRunResponse, type HorizonStrategy } from "../api/client";

type H = "mid" | "long";
const SKIP_CN: Record<string, string> = {
  slots_full: "持仓名额已满，今天不开新仓",
  all_held: "候选股都已在持有中，不重复买入",
  small_cap_below_ma20: "小盘指数在 20 日均线下方，按规则暂停开新仓",
};
const pct = (v?: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const pnl = (v?: number | null) => (v == null ? "" : v > 0 ? "hz-up" : v < 0 ? "hz-down" : "");
const price = (v?: number | null) => (v == null ? "—" : v.toFixed(2));
function detailOf(msg: string) { try { const j = JSON.parse(msg); return typeof j.detail === "string" ? j.detail : msg; } catch { return msg; } }
const todayBj = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);

/* ---------- 手动出票 ---------- */
export function RunPanel({ horizon, list, onClose, onDone }: { horizon: H; list: HorizonStrategy[]; onClose: () => void; onDone: () => void }) {
  const [asOf, setAsOf] = useState(todayBj());
  const [sel, setSel] = useState<string[]>([]);
  const [force, setForce] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>("");
  const [res, setRes] = useState<HorizonRunResponse | null>(null);
  const name = (id: string) => list.find((s) => s.id === id)?.name ?? id;
  async function run() {
    setBusy(true); setErr(""); setRes(null);
    try {
      const r = await api.horizonRun(horizon, { as_of: asOf, ...(sel.length ? { strategy_ids: sel } : {}), ...(force ? { force: true } : {}) });
      setRes(r); onDone();
    } catch (e) {
      const st = (e as { status?: number }).status; const m = detailOf((e as Error).message);
      setErr(st === 409 ? "已有运行中，请稍后再试" : st === 400 ? `日期或参数不合法：${m}` : /请求超时/.test(m) ? "等待超过 15 分钟，可能仍在运行，请稍后刷新" : m);
    } finally { setBusy(false); setConfirm(false); }
  }
  const entries = res ? Object.entries(res.result ?? {}) : [];
  return (
    <section className="panel hz-pad hz-run">
      <div className="hz-sechead"><Play size={16} /><b>手动出票 / 补跑（{horizon === "mid" ? "中线" : "长线"}）</b>
        <span className="hz-muted">与每天 17:35 盘后同一流程；同一天重跑会覆盖当天的出票文件</span>
        <button className="hz-iconbtn" onClick={onClose} aria-label="关闭"><X size={16} /></button></div>
      <div className="hz-run__form">
        <label>信号日 <input type="date" className="hz-select" value={asOf} max={todayBj()} onChange={(e) => setAsOf(e.target.value)} /></label>
        <div className="hz-run__strats">
          <span className="hz-muted">策略（不选=全部到期策略）</span>
          {list.map((s) => (
            <label key={s.id} className={`hz-chk${sel.includes(s.id) ? " on" : ""}`}>
              <input type="checkbox" checked={sel.includes(s.id)} onChange={() => setSel(sel.includes(s.id) ? sel.filter((x) => x !== s.id) : [...sel, s.id])} />{s.name}
            </label>))}
        </div>
        <label className="hz-chk"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />强制重算（force）</label>
      </div>
      {!confirm ? (
        <button className="hz-btn" disabled={busy || !asOf} onClick={() => setConfirm(true)}>{busy ? "运行中…" : "运行"}</button>
      ) : (
        <div className="hz-confirm"><AlertTriangle size={16} />
          将重跑 {asOf} 的{sel.length ? ` ${sel.length} 个策略` : "全部到期策略"}，会覆盖这一天已有的出票结果。确定吗？
          <button className="hz-btn" onClick={run} disabled={busy}>确认运行</button><button className="hz-btn hz-btn--ghost" onClick={() => setConfirm(false)}>取消</button></div>
      )}
      {err && <div className="hz-err">{err}</div>}
      {res && (
        <div className="hz-run__res">
          <div className="hz-muted">{res.as_of} 运行完成{res.force ? "（强制重算）" : ""}</div>
          {entries.length === 0 ? <div>这一天没有到期需要出票的策略（例如长线月度策略只在月末出票）。</div> :
            <ul>{entries.map(([sid, v]) => (
              <li key={sid}><b>{name(sid)}</b>：{typeof v === "number" ? (v > 0 ? `出票 ${v} 只` : "符合条件的股票为 0 只") : v === "no_data" ? "当天数据未就绪，未出票" : typeof v === "object" && v && "skipped" in v ? `跳过 — ${SKIP_CN[v.skipped] ?? v.skipped}` : JSON.stringify(v)}</li>))}</ul>}
        </div>
      )}
    </section>
  );
}

/* ---------- 个股抽屉 ---------- */
export function PickDrawer({ horizon, target, onClose, onOpen }: { horizon: H; target: { sid: string; date: string; code: string }; onClose: () => void; onOpen: (t: { sid: string; date: string; code: string }) => void }) {
  const [d, setD] = useState<HorizonPickDetail | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setD(null); setErr(""); api.horizonPickDetail(horizon, target.sid, target.date, target.code).then(setD).catch((e) => setErr((e as Error).message)); }, [horizon, target.sid, target.date, target.code]);
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  const p = d?.pick;
  return (
    <div className="hz-drawer-mask" onClick={onClose}>
      <aside className="hz-drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="个股详情">
        <header className="hz-drawer__head">
          <div><div className="hz-num hz-muted">{target.code}</div><h3>{d?.name ?? "加载中…"}</h3></div>
          <button className="hz-iconbtn" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </header>
        {err && <div className="hz-err">{err}</div>}
        {d && p && (
          <div className="hz-drawer__body">
            <div className="hz-tags"><span className="hz-tag">{d.strategy_name}</span><span className="hz-tag">信号日 {d.signal_date}</span>
              {d.hold_days ? <span className="hz-tag">持有 {d.hold_days} 交易日</span> : null}<span className="hz-tag">批次 {d.batch_size ?? "—"} 只</span></div>
            <PickStatusLine p={p} />
            <KChart bars={d.bars} p={p} />
            <div className="hz-dl">
              <div><span>买入</span><b className="hz-num">{p.entry_date ?? "—"} @ {price(p.entry_price)}</b></div>
              <div><span>{p.exit_date ? "卖出" : "估值"}</span><b className="hz-num">{(p.exit_date ?? p.mark_date) ?? "—"} @ {price(p.exit_price ?? p.mark_price)}</b></div>
              <div><span>收益</span><b className={`hz-num ${pnl(p.ret)}`}>{pct(p.ret)}</b></div>
              <div><span>持有天数</span><b className="hz-num">{p.days ?? "—"}</b></div>
              <div><span>{p.rule_score_label ?? "规则分"}</span><b className="hz-num">{p.rule_score ?? "—"}{p.rule_score_better ? `（${p.rule_score_better === "lower" ? "越低越优先" : "越高越优先"}）` : ""}</b>{p.rule_score_note ? <span className="hz-score-note">{p.rule_score_note}</span> : null}</div>
              <div><span>批内排名</span><b className="hz-num">{p.selection_rank ?? "—"}</b></div>
            </div>
            <div className="hz-card__sec">入选原因</div><p className="hz-note">{p.selection_reason ?? "—"}</p>
            {d.strategy_summary && (<><div className="hz-card__sec">策略规则</div><p className="hz-note">{d.strategy_summary}</p></>)}
            <div className="hz-muted">价格为不复权价{p.ret_includes_cost === true ? "；已平仓收益已扣 0.3% 成本" : p.ret_includes_cost === false ? `；浮动收益按 ${p.mark_date ?? "最新"} 收盘，未扣 0.3% 成本` : ""}</div>
            {(d.other_signals ?? []).length > 0 && (<>
              <div className="hz-card__sec">该股其他入选记录</div>
              <ul className="hz-other">{d.other_signals!.map((o) => (
                <li key={o.strategy_id + o.signal_date}><button className="hz-link" onClick={() => onOpen({ sid: o.strategy_id, date: o.signal_date, code: target.code })}>{o.signal_date} · {o.strategy_name}</button></li>))}</ul></>)}
          </div>
        )}
      </aside>
    </div>
  );
}

export function PickStatusLine({ p }: { p: HorizonPick }) {
  const bits: string[] = [];
  if (p.cancel_code || p.status === "skipped") bits.push(`已取消买入：${p.cancel_reason ?? "开盘涨停，买不进"}`);
  if ((p.exit_delayed_days ?? 0) > 0) bits.push(`跌停封死，卖出顺延 ${p.exit_delayed_days} 天`);
  if (!bits.length) return null;
  return <div className="hz-warnline">{bits.join(" · ")}</div>;
}

function KChart({ bars, p }: { bars: HorizonBar[]; p: HorizonPick }) {
  if (!bars?.length) return <div className="hz-muted">无日 K 数据</div>;
  const W = 640, Hh = 240, pad = 8, n = bars.length, cw = (W - pad * 2) / n;
  const hi = Math.max(...bars.map((b) => b.high)), lo = Math.min(...bars.map((b) => b.low));
  const y = (v: number) => pad + ((hi - v) / (hi - lo || 1)) * (Hh - pad * 2 - 16);
  const x = (i: number) => pad + i * cw + cw / 2;
  const idx = (d?: string | null) => (d ? bars.findIndex((b) => b.trade_date === d) : -1);
  const ei = idx(p.entry_date), xi = idx(p.exit_date ?? p.mark_date);
  return (
    <svg className="hz-k" viewBox={`0 0 ${W} ${Hh}`} role="img" aria-label="日K（不复权）">
      {ei >= 0 && xi >= 0 && <rect x={x(ei) - cw / 2} y={0} width={x(xi) - x(ei) + cw} height={Hh - 16} className="hz-k__hold" />}
      {bars.map((b, i) => { const up = b.close >= b.open; return (
        <g key={b.trade_date} className={up ? "hz-k__up" : "hz-k__dn"}>
          <line x1={x(i)} x2={x(i)} y1={y(b.high)} y2={y(b.low)} />
          <rect x={x(i) - cw * 0.35} width={cw * 0.7} y={y(Math.max(b.open, b.close))} height={Math.max(1, Math.abs(y(b.open) - y(b.close)))} />
          <title>{`${b.trade_date} 开${b.open} 高${b.high} 低${b.low} 收${b.close}`}</title></g>); })}
      {ei >= 0 && p.entry_price != null && <Marker x={x(ei)} y={y(p.entry_price)} label={`买 ${p.entry_price}`} kind="buy" />}
      {xi >= 0 && (p.exit_price ?? p.mark_price) != null && <Marker x={x(xi)} y={y((p.exit_price ?? p.mark_price)!)} label={`${p.exit_date ? "卖" : "估"} ${p.exit_price ?? p.mark_price}`} kind="sell" />}
      <text x={pad} y={Hh - 2} className="hz-k__axis">{bars[0].trade_date}</text>
      <text x={W - pad} y={Hh - 2} textAnchor="end" className="hz-k__axis">{bars[n - 1].trade_date}</text>
    </svg>
  );
}
const Marker = ({ x, y, label, kind }: { x: number; y: number; label: string; kind: "buy" | "sell" }) => (
  <g className={`hz-k__m hz-k__m--${kind}`}><circle cx={x} cy={y} r={5} /><text x={x + 8} y={kind === "buy" ? y + 14 : y - 8}>{label}</text></g>
);
