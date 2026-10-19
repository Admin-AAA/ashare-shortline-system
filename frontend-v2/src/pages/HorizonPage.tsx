import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, CalendarClock, Layers, Archive } from "lucide-react";
import { api, type HorizonBatch, type HorizonMetrics, type HorizonPick, type HorizonStrategy, type HorizonStrategiesResponse, type HorizonPicksResponse } from "../api/client";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";
import { useApi } from "../app/useApi";
import "../styles/horizon.css";
import { RunPanel, PickDrawer, PickStatusLine } from "./HorizonExtras";

type Target = { sid: string; date: string; code: string };
const SCHEDULE_OVERRIDE: Record<string, string> = { "mid-block-trade-premium": "每晚约 21:00 大宗数据发布后选股" };

type H = "mid" | "long";
type View = "today" | "review" | "strategies";

/** 中线 / 长线：按一级入口分三种视图（今日=待买与持有，复盘=按批次收益，策略=画像与淘汰清单）。
 * 只用 /api/horizons/{h}/strategies 与 /picks 的真实字段；涨红跌绿只用于收益。 */
export function HorizonPage({ horizon, view = "strategies" }: { horizon: H; view?: View }) {
  const strategies = useApi<HorizonStrategiesResponse>(() => api.horizonStrategies(horizon), [horizon]);
  const picks = useApi<HorizonPicksResponse | null>(() => api.horizonPicks(horizon, undefined, 60), [horizon]);
  const hz = horizon === "mid" ? "中线" : "长线";
  const [runOpen, setRunOpen] = useState(false);
  const [target, setTarget] = useState<Target | null>(null);
  const [rev, setRev] = useState(0);

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
      <Hero horizon={horizon} view={view} list={list} batches={batches} onRun={() => setRunOpen(true)} />
      {runOpen && <RunPanel horizon={horizon} list={list} onClose={() => setRunOpen(false)} onDone={() => { picks.refetch(); setRev((n) => n + 1); }} />}
      {target && <PickDrawer horizon={horizon} target={target} onClose={() => setTarget(null)} onOpen={setTarget} />}
      {view === "strategies" && (
        <>
          {list.length === 0
            ? <section className="panel hz-pad"><EmptyState icon={<Layers size={32} />} title={`暂无通过回测的${hz}策略`} reason="候选思路在最近12个月或历史分段回测中不达标，未上线；淘汰原因见下方。" /></section>
            : <div className="hz-grid">{list.map((s) => <StrategyCard key={s.id} s={s} />)}</div>}
          <Rejected items={rejected} />
        </>
      )}
      {view === "today" && picksBlock((b) => <TodayView batches={b} list={list} onPick={setTarget} />)}
      {view === "review" && <ReviewView key={horizon + rev} horizon={horizon} list={list} onPick={setTarget} />}
    </div>
  );
}

/* ---------- 公共 ---------- */
const pct = (v?: number | null, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const pnl = (v?: number | null) => (v == null ? "" : v > 0 ? "hz-up" : v < 0 ? "hz-down" : "");
const STATUS: Record<string, [string, string]> = {
  pending: ["待买入", "amber"], holding: ["持有中", "blue"], closed: ["已平仓", "gray"], skipped: ["开盘涨停·已取消", "gray"],
};
const Status = ({ s }: { s: string }) => { const [l, c] = STATUS[s] ?? [s, "gray"]; return <span className={`hz-pill hz-pill--${c}`}>{l}</span>; };
const SCHEDULE: Record<string, string> = { weekly: "每周五盘后", daily: "每日盘后", month_end: "月末盘后", monthly: "每月" };
const fmtDate = (d?: string) => (!d ? "—" : /^\d{8}$/.test(d) ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d);

function Hero({ horizon, view, list, batches, onRun }: { horizon: H; view: View; list: HorizonStrategy[]; batches: HorizonBatch[]; onRun: () => void }) {
  const hz = horizon === "mid" ? "中线" : "长线";
  const sub = { today: "今日待买 · 持有中", review: "按批次复盘", strategies: "策略画像" }[view];
  const pending = batches.filter((b) => b.status === "pending").reduce((n, b) => n + b.picks.filter((p) => p.status === "pending").length, 0);
  const holding = batches.reduce((n, b) => n + b.picks.filter((p) => p.status === "holding").length, 0);
  const closed = batches.filter((b) => b.status === "closed").length;
  const holdRange = list.length ? [...new Set(list.map((s) => s.hold_days))].sort((a, b) => a - b).join(" / ") : null;
  return (
    <section className="panel hz-hero">
      <div>
        <div className="panel__kicker">{hz} · {sub}</div>
        <h2 className="hz-hero__title">{hz}策略{horizon === "mid" ? "（持有 20 个交易日，约 4 周）" : "（持有 60 个交易日，约 3 个月）"}</h2>
        <div className="hz-muted">规则打分、固定持有期出场；回测含 0.3%/笔成本，开盘涨停取消买入、停牌顺延买入、跌停封死顺延卖出。</div>
        <button className="hz-btn" style={{ marginTop: 10 }} onClick={onRun}>手动出票 / 补跑</button>
      </div>
      <div className="hz-hero__kpis">
        <Kpi label="现役策略" value={String(list.length)} />
        <Kpi label="持有期（交易日）" value={holdRange ?? "—"} />
        <Kpi label="待买入" value={`${pending} 只`} tone="amber" />
        <Kpi label="持有中" value={`${holding} 只`} tone="blue" />
        <Kpi label="已平仓批次（近期）" value={String(closed)} />
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
  const allPh = s.metrics.phases ?? {};
  const phases = Object.entries(allPh).filter(([k]) => !k.startsWith("N3")).map(([k, v]) => [k.startsWith("R12") ? "近12个月" : k, v] as [string, HorizonMetrics])
    .reverse(); // 倒序：近12个月在最上，2019 在最下（接口按时间正序返回）
  const n3 = Object.entries(allPh).find(([k]) => k.startsWith("N3"))?.[1];
  const maxAbs = Math.max(1, ...phases.map(([, p]) => Math.abs(p.avg ?? 0)));
  void 0;
  const positive = phases.filter(([, p]) => (p.avg ?? 0) > 0).length;
  return (
    <article className="panel hz-card">
      <header className="hz-card__head">
        <h3>{s.name}</h3>
        <div className="hz-tags">
          <span className="hz-tag"><CalendarClock size={12} /> {SCHEDULE_OVERRIDE[s.id] ?? `${SCHEDULE[s.schedule] ?? s.schedule}选股`}</span>
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
          <div className="hz-card__sec">历史分段 · 平均收益/笔 <span className="hz-muted">（{phases.length} 个独立阶段，{positive}/{phases.length} 段为正）</span></div>
          <div className="hz-bars">
            {phases.map(([k, p]) => {
              const v = p.avg ?? 0;
              const w = (Math.abs(v) / maxAbs) * 50;
              return (
                <div key={k} className="hz-bar" title={`${k}\n笔数 ${p.n ?? "—"} · 胜率 ${p.win ?? "—"}% · PF ${p.pf ?? "—"}\n组合 ${pct(p.tot, 1)} · 回撤 ${p.mdd ?? "—"}%`}>
                  <span className="hz-bar__k">{k.replace(/^[A-Z]\d+\s*/, "").replace("2023-04~2023-12", "2023-04~12")}</span>
                  <span className="hz-bar__track"><span className={`hz-bar__fill ${v >= 0 ? "pos" : "neg"}`} style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} /></span>
                  <span className={`hz-bar__v ${pnl(v)}`}>{pct(p.avg)}</span>
                  <span className="hz-bar__n">PF {p.pf ?? "—"}</span>
                </div>
              );
            })}
          </div>
          <div className="hz-bar hz-bar--sum" title="与上面各阶段重叠，单独作为汇总，不计入阶段数">
            <span className="hz-bar__k">2024-01 至今（约2.8年）</span>
            {n3 ? (<><span className="hz-muted">{n3.n ?? "—"} 笔 · 胜率 {n3.win ?? "—"}% · 组合 {pct(n3.tot, 1)} · 回撤 {n3.mdd ?? "—"}%</span>
              <span className={`hz-bar__v ${pnl(n3.avg)}`}>{pct(n3.avg)}</span><span className="hz-bar__n">PF {n3.pf ?? "—"}</span></>)
              : <span className="hz-muted">无</span>}
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
function TodayView({ batches, list, onPick }: { batches: HorizonBatch[]; list: HorizonStrategy[]; onPick: (t: Target) => void }) {
  const active = batches.filter((b) => b.status === "pending" || b.status === "holding");
  const withPicks = new Set(batches.map((b) => b.strategy_id));
  const noPicks = list.filter((s) => !withPicks.has(s.id));
  const latest = active.map((b) => b.signal_date).sort().slice(-1)[0];
  return (
    <>
      {active.length ? <div className="hz-muted hz-hint">最近信号日 {latest} · 待买入的在下一交易日开盘买入；开盘涨停则取消买入，停牌顺延买入。</div>
        : <section className="panel hz-pad"><EmptyState title="当前没有待买入或持有中的批次" reason="到期平仓的批次在「复盘」里看。" /></section>}
      {active.map((b) => <BatchCard key={b.signal_date + b.strategy_id} b={b} defaultOpen list={list} onPick={onPick} />)}
      {noPicks.length > 0 && (
        <section className="panel hz-pad">
          <div className="hz-sechead"><b>还没有出票的策略</b><span className="hz-muted">{noPicks.length} 个</span></div>
          <div className="hz-rej">{noPicks.map((s) => (
            <div key={s.id} className="hz-rej__item hz-rej__item--wait"><div className="hz-rej__name">{s.name}</div>
              <div className="hz-rej__why">{s.next_batch_note ?? `${SCHEDULE_OVERRIDE[s.id] ?? SCHEDULE[s.schedule] ?? s.schedule}出票，暂无批次`}</div></div>))}</div>
        </section>)}
    </>
  );
}

/* ---------- 复盘（服务端分页） ---------- */
const PAGE_SIZE = 20;
function ReviewView({ horizon, list, onPick }: { horizon: H; list: HorizonStrategy[]; onPick: (t: Target) => void }) {
  const [sid, setSid] = useState<string>("all");
  const [page, setPage] = useState(1);
  const [st, setSt] = useState<string>("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"date" | "ret_desc" | "ret_asc">("date");
  const res = useApi<HorizonPicksResponse>(() => api.horizonPicksPage(horizon, page, PAGE_SIZE, sid === "all" ? undefined : sid), [horizon, page, sid]);
  const kw = q.trim();
  const batches = res.data?.batches ?? [];
  const shown = batches
    .filter((b) => st === "all" || b.status === st || b.picks.some((p) => p.status === st))
    .filter((b) => !kw || b.picks.some((p) => p.code.includes(kw) || p.name.includes(kw)))
    .sort((a, b) => sort === "date" ? b.signal_date.localeCompare(a.signal_date)
      : ((sort === "ret_desc" ? -1 : 1) * ((a.avg_ret ?? -1e9) - (b.avg_ret ?? -1e9))));
  const closed = shown.filter((b) => b.status === "closed" && b.avg_ret != null);
  const closedPicks = shown.flatMap((b) => b.picks).filter((p) => p.ret != null && p.status === "closed");
  const cancelled = shown.flatMap((b) => b.picks).filter(isCancelled).length;
  const avgBatch = closed.length ? closed.reduce((a, b) => a + (b.avg_ret ?? 0), 0) / closed.length : null;
  const avgPick = closedPicks.length ? closedPicks.reduce((a, p) => a + (p.ret ?? 0), 0) / closedPicks.length : null;
  const winPick = closedPicks.length ? (closedPicks.filter((p) => (p.ret ?? 0) > 0).length / closedPicks.length) * 100 : null;
  const total = res.data?.total ?? 0, pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = st !== "all" || !!kw;
  return (
    <>
      <section className="panel hz-pad">
        <div className="hz-sechead">
          <b>前瞻批次复盘</b>
          <span className="hz-muted">共 {total} 个批次 / {res.data?.total_picks ?? 0} 只个股{sid === "all" ? "" : "（当前策略）"} · 第 {page}/{pages} 页，每页 {PAGE_SIZE} 批</span>
          <div className="hz-filters">
            <input className="hz-select" placeholder="代码 / 名称（本页）" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="hz-select" value={sid} onChange={(e) => { setSid(e.target.value); setPage(1); }}>
              <option value="all">全部策略</option>
              {list.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
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
        <div className="hz-muted" style={{ marginBottom: 8 }}>以下统计仅针对本页{filtered ? "筛选后" : ""}的 {shown.length} 个批次；已平仓收益含 0.3% 成本。</div>
        <div className="hz-metrics">
          <Kpi label="已平仓批次" value={String(closed.length)} />
          <Kpi label="批次等权平均" value={pct(avgBatch)} cls={pnl(avgBatch)} />
          <Kpi label={`每笔平均（${closedPicks.length} 笔已平仓）`} value={pct(avgPick)} cls={pnl(avgPick)} />
          <Kpi label="个股胜率" value={winPick == null ? "—" : `${winPick.toFixed(1)}%`} />
          <Kpi label="开盘涨停取消买入" value={`${cancelled} 只`} tone={cancelled ? "amber" : undefined} />
        </div>
      </section>
      {res.loading ? <PageSkeleton title="加载批次" /> : res.error ? <ErrorPage title="批次加载失败" message={res.error} onRetry={res.refetch} /> : <>
        {shown.length === 0 && <section className="panel hz-pad"><EmptyState title="没有符合条件的批次" reason="换个策略/状态、清空搜索或翻页。" /></section>}
        {shown.map((b, i) => <BatchCard key={b.signal_date + b.strategy_id} b={b} defaultOpen={i === 0 || !!kw} list={list} highlight={kw} onPick={onPick} />)}
      </>}
      <div className="hz-pager">
        <button className="hz-btn hz-btn--ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
        <span className="hz-num">{page} / {pages}</span>
        <button className="hz-btn hz-btn--ghost" disabled={!res.data?.has_more} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
    </>
  );
}

const isCancelled = (p: HorizonPick) => p.cancel_code === "limit_up_open" || p.status === "skipped";

/* ---------- 批次 ---------- */
const better = (b?: string | null) => (b === "lower" ? "越低越优先" : b === "higher" ? "越高越优先" : "");
function BatchCard({ b, defaultOpen, list, highlight, onPick }: { b: HorizonBatch; defaultOpen?: boolean; list: HorizonStrategy[]; highlight?: string; onPick: (t: Target) => void }) {
  const [open, setOpen] = useState(!!defaultOpen);
  const hold = list.find((s) => s.id === b.strategy_id)?.hold_days;
  const cancelN = b.picks.filter(isCancelled).length;
  const first = b.picks.find((p) => p.rule_score_label);
  const hasHolding = b.picks.some((p) => p.status === "holding");
  const markDate = b.picks.find((p) => p.status === "holding" && p.mark_date)?.mark_date;
  const retHead = b.status === "closed" ? "收益（含成本）" : hasHolding ? `浮动收益（按 ${markDate ?? "最新"} 收盘，未扣 0.3% 成本）` : "收益";
  // 同一批次只有一个策略，规则分单位一致；按批内排名排序，不跨策略比较
  const picks = [...b.picks].sort((x, y) => (x.selection_rank ?? 999) - (y.selection_rank ?? 999));
  return (
    <section className="panel hz-batch">
      <button className="hz-batch__head" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span className="hz-num">{b.signal_date}</span>
        <span className="hz-batch__name">{b.strategy_name}</span>
        <Status s={b.status} />
        <span className="hz-muted">{b.picks.length} 只{cancelN ? `（${cancelN} 只开盘涨停取消）` : ""}{hold ? ` · 持 ${hold} 日` : ""}</span>
        <span className={`hz-batch__ret hz-num ${pnl(b.avg_ret)}`}>{b.avg_ret == null ? "—" : `${b.status === "closed" ? "平均" : "浮动平均"} ${pct(b.avg_ret)}`}</span>
      </button>
      {open && (
        <div className="hz-tablewrap">
          <table className="hz-table">
            <thead><tr><th className="r">排名</th><th>代码</th><th>名称</th><th className="r">{first ? `${first.rule_score_label}（${better(first.rule_score_better)}）` : "规则分"}</th><th>状态</th><th>买入</th><th>卖出 / 估值</th><th className="r">天数</th><th className="r">{retHead}</th><th>入选原因</th></tr></thead>
            <tbody>
              {picks.map((p) => (
                <tr key={p.code} className={`hz-row${isCancelled(p) ? " hz-row--cancel" : ""}${highlight && (p.code.includes(highlight) || p.name.includes(highlight)) ? " hz-hl" : ""}`}
                  onClick={() => onPick({ sid: b.strategy_id, date: b.signal_date, code: p.code })} title="点击查看个股详情">
                  <td className="r hz-num">{p.selection_rank ?? "—"}</td>
                  <td className="hz-num">{p.code}</td><td>{p.name}</td>
                  <td className="r hz-num">{p.rule_score ?? "—"}</td>
                  <td><Status s={isCancelled(p) ? "skipped" : p.status} />{isCancelled(p) || (p.exit_delayed_days ?? 0) > 0 ? <PickStatusLine p={p} /> : null}</td>
                  <td className="hz-num">{p.entry_date ? `${p.entry_date} @${p.entry_price ?? "—"}` : "—"}</td>
                  <td className="hz-num">{p.exit_date ? `${p.exit_date} @${p.exit_price ?? "—"}` : p.mark_date ? `${p.mark_date} @${p.mark_price ?? "—"}（估）` : "—"}</td>
                  <td className="r hz-num">{p.days ?? "—"}</td>
                  <td className={`r hz-num ${pnl(p.ret)}`}>{pct(p.ret)}</td>
                  <td className="hz-muted hz-reason">{p.selection_reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
