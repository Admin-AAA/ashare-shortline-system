import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, CalendarClock, Layers, Archive } from "lucide-react";
import { api, type HorizonBatch, type HorizonMetrics, type HorizonPick, type HorizonStrategy, type HorizonStrategiesResponse, type HorizonPicksResponse } from "../api/client";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";
import { useApi } from "../app/useApi";
import "../styles/horizon.css";

type H = "mid" | "long";
type View = "today" | "review" | "strategies";

/** 中线 / 长线：按一级入口分三种视图（今日=待买与持有，复盘=按批次收益，策略=画像与淘汰清单）。
 * 只用 /api/horizons/{h}/strategies 与 /picks 的真实字段；涨红跌绿只用于收益。 */
export function HorizonPage({ horizon, view = "strategies" }: { horizon: H; view?: View }) {
  const strategies = useApi<HorizonStrategiesResponse>(() => api.horizonStrategies(horizon), [horizon]);
  const picks = useApi<HorizonPicksResponse | null>(() => api.horizonPicks(horizon, undefined, 60), [horizon]);
  const hz = horizon === "mid" ? "中线" : "长线";

  if (strategies.loading) return <PageSkeleton title={`${hz}加载中`} />;
  if (strategies.error) return <ErrorPage title={`${hz}策略加载失败`} message={strategies.error} onRetry={strategies.refetch} />;
  const list = strategies.data?.strategies ?? [];
  const rejected = strategies.data?.rejected ?? [];
  const batches = picks.data?.batches ?? [];

  const picksBlock = (render: (b: HorizonBatch[]) => React.ReactNode) =>
    picks.loading ? <PageSkeleton title="加载选股" /> :
    picks.error ? <ErrorPage title="选股加载失败" message={picks.error} onRetry={picks.refetch} /> : render(batches);

  return (
    <div className="hz">
      <Hero horizon={horizon} view={view} list={list} batches={batches} />
      {view === "strategies" && (
        <>
          {list.length === 0
            ? <section className="panel hz-pad"><EmptyState icon={<Layers size={32} />} title={`暂无通过回测的${hz}策略`} reason="候选思路在最近12个月或历史分段回测中不达标，未上线；淘汰原因见下方。" /></section>
            : <div className="hz-grid">{list.map((s) => <StrategyCard key={s.id} s={s} />)}</div>}
          <Rejected items={rejected} />
        </>
      )}
      {view === "today" && picksBlock((b) => <TodayView batches={b} list={list} />)}
      {view === "review" && picksBlock((b) => <ReviewView batches={b} list={list} />)}
    </div>
  );
}

/* ---------- 公共 ---------- */
const pct = (v?: number | null, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const pnl = (v?: number | null) => (v == null ? "" : v > 0 ? "hz-up" : v < 0 ? "hz-down" : "");
const STATUS: Record<string, [string, string]> = {
  pending: ["待买入", "amber"], holding: ["持有中", "blue"], closed: ["已平仓", "gray"], skipped: ["涨停买不进", "gray"],
};
const Status = ({ s }: { s: string }) => { const [l, c] = STATUS[s] ?? [s, "gray"]; return <span className={`hz-pill hz-pill--${c}`}>{l}</span>; };
const SCHEDULE: Record<string, string> = { weekly: "每周五盘后", daily: "每日盘后", month_end: "月末盘后", monthly: "每月" };
const fmtDate = (d?: string) => (!d ? "—" : /^\d{8}$/.test(d) ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d);

function Hero({ horizon, view, list, batches }: { horizon: H; view: View; list: HorizonStrategy[]; batches: HorizonBatch[] }) {
  const hz = horizon === "mid" ? "中线" : "长线";
  const sub = { today: "今日待买 · 持有中", review: "按批次复盘", strategies: "策略画像" }[view];
  const pending = batches.filter((b) => b.status === "pending").reduce((n, b) => n + b.picks.length, 0);
  const holding = batches.filter((b) => b.status === "holding").reduce((n, b) => n + b.picks.length, 0);
  const closed = batches.filter((b) => b.status === "closed").length;
  const holdRange = list.length ? [...new Set(list.map((s) => s.hold_days))].sort((a, b) => a - b).join(" / ") : null;
  return (
    <section className="panel hz-hero">
      <div>
        <div className="panel__kicker">{hz} · {sub}</div>
        <h2 className="hz-hero__title">{hz}策略{horizon === "mid" ? "（持有约 2–8 周）" : "（持有数月）"}</h2>
        <div className="hz-muted">规则打分、固定持有期出场；回测含 0.3%/笔成本，涨停不买、跌停顺延卖出。</div>
      </div>
      <div className="hz-hero__kpis">
        <Kpi label="现役策略" value={String(list.length)} />
        <Kpi label="持有期（交易日）" value={holdRange ?? "—"} />
        <Kpi label="待买入" value={`${pending} 只`} tone="amber" />
        <Kpi label="持有中" value={`${holding} 只`} tone="blue" />
        <Kpi label="已平仓批次" value={String(closed)} />
      </div>
    </section>
  );
}
const Kpi = ({ label, value, tone, cls }: { label: string; value: string; tone?: string; cls?: string }) => (
  <div className={`hz-kpi${tone ? ` hz-kpi--${tone}` : ""}`}><div className="hz-kpi__l">{label}</div><div className={`hz-kpi__v ${cls ?? ""}`}>{value}</div></div>
);

/* ---------- 策略卡 ---------- */
function StrategyCard({ s }: { s: HorizonStrategy }) {
  const [open, setOpen] = useState(false);
  const m = s.metrics.recent_12m ?? {};
  const ex = m as HorizonMetrics & { excess?: number; bench?: number };
  const phases = Object.entries(s.metrics.phases ?? {}).filter(([k]) => !k.startsWith("R12"));
  const maxAbs = Math.max(1, ...phases.map(([, p]) => Math.abs(p.avg ?? 0)));
  const positive = phases.filter(([, p]) => (p.avg ?? 0) > 0).length;
  return (
    <article className="panel hz-card">
      <header className="hz-card__head">
        <h3>{s.name}</h3>
        <div className="hz-tags">
          <span className="hz-tag"><CalendarClock size={12} /> {SCHEDULE[s.schedule] ?? s.schedule}选股</span>
          <span className="hz-tag">每次 Top {s.top_n}</span>
          <span className="hz-tag">持有 {s.hold_days} 交易日</span>
        </div>
      </header>
      <p className="hz-summary">{s.summary}</p>
      <div className="hz-card__sec">最近 12 个月{s.metrics.window ? <span className="hz-muted"> · {s.metrics.window}</span> : null}</div>
      <div className="hz-metrics">
        <Kpi label="笔数" value={m.n != null ? String(Math.round(m.n)) : "—"} />
        <Kpi label="胜率" value={m.win != null ? `${m.win.toFixed(1)}%` : "—"} />
        <Kpi label="平均/笔" value={pct(m.avg)} cls={pnl(m.avg)} />
        <Kpi label="PF" value={m.pf != null ? m.pf.toFixed(2) : "—"} />
        <Kpi label="组合收益" value={pct(m.tot, 1)} cls={pnl(m.tot)} />
        <Kpi label="最大回撤" value={m.mdd != null ? `${m.mdd.toFixed(1)}%` : "—"} />
        {ex.excess != null && <Kpi label="每笔超额" value={pct(ex.excess)} cls={pnl(ex.excess)} />}
      </div>
      {phases.length > 0 && (
        <>
          <div className="hz-card__sec">历史分段 · 平均收益/笔 <span className="hz-muted">（{positive}/{phases.length} 段为正）</span></div>
          <div className="hz-bars">
            {phases.map(([k, p]) => {
              const v = p.avg ?? 0;
              const w = (Math.abs(v) / maxAbs) * 50;
              return (
                <div key={k} className="hz-bar" title={`${k}\n笔数 ${p.n ?? "—"} · 胜率 ${p.win ?? "—"}% · PF ${p.pf ?? "—"}\n组合 ${pct(p.tot, 1)} · 回撤 ${p.mdd ?? "—"}%`}>
                  <span className="hz-bar__k">{k.replace(/^[A-Z]\d+\s*/, "")}</span>
                  <span className="hz-bar__track"><span className={`hz-bar__fill ${v >= 0 ? "pos" : "neg"}`} style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} /></span>
                  <span className={`hz-bar__v ${pnl(v)}`}>{pct(p.avg)}</span>
                  <span className="hz-bar__n">PF {p.pf ?? "—"}</span>
                </div>
              );
            })}
          </div>
        </>
      )}
      {s.metrics.note && (
        <button className="hz-link" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} 回测口径说明</button>
      )}
      {open && <p className="hz-note">{s.metrics.note}</p>}
    </article>
  );
}

function Rejected({ items }: { items: { name: string; reason: string }[] }) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, 6);
  return (
    <section className="panel hz-pad">
      <div className="hz-sechead"><Archive size={16} /> <b>回测未通过、已淘汰的思路</b><span className="hz-muted">共 {items.length} 个 · 不出票</span></div>
      <div className="hz-rej">
        {shown.map((r) => (<div key={r.name} className="hz-rej__item"><div className="hz-rej__name">{r.name}</div><div className="hz-rej__why">{r.reason}</div></div>))}
      </div>
      {items.length > 6 && <button className="hz-link" onClick={() => setAll(!all)}>{all ? "收起" : `展开全部 ${items.length} 个`}</button>}
    </section>
  );
}

/* ---------- 今日 ---------- */
function TodayView({ batches, list }: { batches: HorizonBatch[]; list: HorizonStrategy[] }) {
  const active = batches.filter((b) => b.status === "pending" || b.status === "holding");
  if (!active.length) return <section className="panel hz-pad"><EmptyState title="当前没有待买入或持有中的批次" reason="信号日盘后生成选股；到期平仓的批次在「复盘」里看。" /></section>;
  const latest = active.map((b) => b.signal_date).sort().slice(-1)[0];
  return (
    <>
      <div className="hz-muted hz-hint">最近信号日 {latest} · 待买入的在下一交易日开盘买入；涨停开盘不买。</div>
      {active.map((b) => <BatchCard key={b.signal_date + b.strategy_id} b={b} defaultOpen list={list} />)}
    </>
  );
}

/* ---------- 复盘 ---------- */
function ReviewView({ batches, list }: { batches: HorizonBatch[]; list: HorizonStrategy[] }) {
  const [sid, setSid] = useState<string>("all");
  const [st, setSt] = useState<string>("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"date" | "ret_desc" | "ret_asc">("date");
  const ids = useMemo(() => [...new Set(batches.map((b) => b.strategy_id))], [batches]);
  const kw = q.trim();
  const shown = batches
    .filter((b) => sid === "all" || b.strategy_id === sid)
    .filter((b) => st === "all" || b.status === st || b.picks.some((p) => p.status === st))
    .filter((b) => !kw || b.picks.some((p) => p.code.includes(kw) || p.name.includes(kw)))
    .sort((a, b) => sort === "date" ? b.signal_date.localeCompare(a.signal_date)
      : ((sort === "ret_desc" ? -1 : 1) * ((a.avg_ret ?? -1e9) - (b.avg_ret ?? -1e9))));
  const closed = shown.filter((b) => b.status === "closed" && b.avg_ret != null);
  const closedPicks = closed.flatMap((b) => b.picks).filter((p) => p.ret != null && p.status === "closed");
  const skipped = shown.flatMap((b) => b.picks).filter((p) => p.status === "skipped").length;
  const avgBatch = closed.length ? closed.reduce((a, b) => a + (b.avg_ret ?? 0), 0) / closed.length : null;
  const winPick = closedPicks.length ? (closedPicks.filter((p) => (p.ret ?? 0) > 0).length / closedPicks.length) * 100 : null;
  if (!batches.length) return <section className="panel hz-pad"><EmptyState title="还没有选股记录" reason="信号日盘后生成。" /></section>;
  return (
    <>
      <section className="panel hz-pad">
        <div className="hz-sechead">
          <b>前瞻批次复盘</b><span className="hz-muted">近 {batches.length} 个批次（接口返回范围）</span>
          <div className="hz-filters">
            <input className="hz-select" placeholder="代码 / 名称" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="hz-select" value={sid} onChange={(e) => setSid(e.target.value)}>
              <option value="all">全部策略</option>
              {ids.map((i) => <option key={i} value={i}>{batches.find((b) => b.strategy_id === i)?.strategy_name ?? i}</option>)}
            </select>
            <select className="hz-select" value={st} onChange={(e) => setSt(e.target.value)}>
              <option value="all">全部状态</option>
              {Object.entries(STATUS).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <select className="hz-select" value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
              <option value="date">按信号日（新→旧）</option><option value="ret_desc">按批次收益（高→低）</option><option value="ret_asc">按批次收益（低→高）</option>
            </select>
          </div>
        </div>
        <div className="hz-metrics">
          <Kpi label="批次（筛选后）" value={String(shown.length)} />
          <Kpi label="已平仓批次" value={String(closed.length)} />
          <Kpi label="批次平均收益" value={pct(avgBatch)} cls={pnl(avgBatch)} />
          <Kpi label="已平仓个股" value={String(closedPicks.length)} />
          <Kpi label="个股胜率" value={winPick == null ? "—" : `${winPick.toFixed(1)}%`} />
          <Kpi label="涨停买不进（取消）" value={`${skipped} 只`} tone={skipped ? "amber" : undefined} />
        </div>
      </section>
      {shown.length === 0 && <section className="panel hz-pad"><EmptyState title="没有符合筛选条件的批次" reason="换个策略/状态或清空搜索。" /></section>}
      {shown.map((b, i) => <BatchCard key={b.signal_date + b.strategy_id} b={b} defaultOpen={i === 0 || !!kw} list={list} highlight={kw} />)}
    </>
  );
}

/* ---------- 批次 ---------- */
function reasonOf(p: HorizonPick & Record<string, unknown>): string {
  const r: string[] = [];
  if (typeof p.ratio === "number") r.push(`回购上限占市值 ${(p.ratio * 100).toFixed(2)}%`);
  if (typeof p.forecast_type === "string") r.push(`${p.forecast_type}${typeof p.p_change_min === "number" ? ` 下限 +${p.p_change_min.toFixed(0)}%` : ""}`);
  if (typeof p.dt_netprofit_yoy === "number") r.push(`扣非同比 ${p.dt_netprofit_yoy.toFixed(0)}%${typeof p.prev_dt_netprofit_yoy === "number" ? `（上期 ${p.prev_dt_netprofit_yoy.toFixed(0)}%）` : ""}`);
  if (typeof p.ann_date === "string") r.push(`公告 ${fmtDate(p.ann_date)}`);
  return r.join(" · ");
}

function BatchCard({ b, defaultOpen, list, highlight }: { b: HorizonBatch; defaultOpen?: boolean; list: HorizonStrategy[]; highlight?: string }) {
  const [open, setOpen] = useState(!!defaultOpen);
  const hold = list.find((s) => s.id === b.strategy_id)?.hold_days;
  const hasReason = b.picks.some((p) => reasonOf(p as HorizonPick & Record<string, unknown>));
  return (
    <section className="panel hz-batch">
      <button className="hz-batch__head" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span className="hz-num">{b.signal_date}</span>
        <span className="hz-batch__name">{b.strategy_name}</span>
        <Status s={b.status} />
        <span className="hz-muted">{b.picks.length} 只{b.picks.some((p) => p.status === "skipped") ? `（${b.picks.filter((p) => p.status === "skipped").length} 只涨停未买）` : ""}{hold ? ` · 持 ${hold} 日` : ""}</span>
        <span className={`hz-batch__ret hz-num ${pnl(b.avg_ret)}`}>{b.avg_ret == null ? "—" : `平均 ${pct(b.avg_ret)}`}</span>
      </button>
      {open && (
        <div className="hz-tablewrap">
          <table className="hz-table">
            <thead><tr><th>代码</th><th>名称</th><th className="r">总市值(亿)</th><th>状态</th><th>买入日</th><th>卖出/估值日</th><th className="r">持有天数</th><th className="r">收益</th>{hasReason && <th>入选依据</th>}</tr></thead>
            <tbody>
              {b.picks.map((p) => (
                <tr key={p.code} className={highlight && (p.code.includes(highlight) || p.name.includes(highlight)) ? "hz-hl" : undefined}>
                  <td className="hz-num">{p.code}</td><td>{p.name}</td>
                  <td className="r hz-num">{(p.total_mv / 1e4).toFixed(1)}</td>
                  <td><Status s={p.status} /></td>
                  <td className="hz-num">{p.entry_date ?? "—"}</td>
                  <td className="hz-num">{p.exit_date ?? p.mark_date ?? "—"}</td>
                  <td className="r hz-num">{p.days ?? "—"}</td>
                  <td className={`r hz-num ${pnl(p.ret)}`}>{pct(p.ret)}</td>
                  {hasReason && <td className="hz-muted">{reasonOf(p as HorizonPick & Record<string, unknown>) || "—"}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
