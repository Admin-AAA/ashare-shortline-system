import { useMemo, useState } from "react";
import { api, type HorizonMetricsResponse, type HorizonOrigin, type HorizonRunsResponse, type HorizonStats, type HorizonTrade, type HorizonTradesResponse } from "../api/client";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";
import { useApi } from "../app/useApi";
import "../styles/horizon.css";
import { PickDrawer, PickStatusLine } from "./HorizonExtras";

/** 中线 / 长线的「诊断 / 研究」「持仓 / 待卖」「交易明细」「运行记录」四页。
 * 数据全部来自 /api/horizons/{h}/metrics|trades|runs，口径与「按批次复盘」一致：
 * 只有已平仓笔计入胜率/盈亏比（含 0.3% 成本），持有中为浮动收益。 */
type H = "mid" | "long";
type Target = { sid: string; date: string; code: string };

const hzName = (h: H) => (h === "mid" ? "中线" : "长线");
const pct = (v?: number | null, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const pnl = (v?: number | null) => (v == null ? "" : v > 0 ? "hz-up" : v < 0 ? "hz-down" : "");
const num = (v?: number | null, d = 2) => (v == null ? "—" : v.toFixed(d));
const ORIGIN_LABEL: Record<HorizonOrigin, string> = { live: "实盘", replay: "回放", legacy: "旧数据" };
const ORIGIN_TIP: Record<HorizonOrigin, string> = {
  live: "盘后流程真实出票",
  replay: "用现行策略历史回放重建",
  legacy: "没有来源标记、也没有当天运行记录的旧文件，不当实盘算",
};
const STATUS: Record<string, [string, string]> = {
  pending: ["待买入", "amber"], holding: ["持有中", "blue"], closed: ["已平仓", "gray"], skipped: ["开盘涨停·已取消", "gray"],
};
const Status = ({ s }: { s: string }) => { const [l, c] = STATUS[s] ?? [s, "gray"]; return <span className={`hz-pill hz-pill--${c}`}>{l}</span>; };
const Origin = ({ o }: { o: HorizonOrigin }) => <span className={`hz-pill hz-pill--${o === "live" ? "blue" : o === "replay" ? "amber" : "gray"}`} title={ORIGIN_TIP[o]}>{ORIGIN_LABEL[o]}</span>;
const Kpi = ({ label, value, cls, sub }: { label: string; value: string; cls?: string; sub?: string }) => (
  <div className="hz-kpi"><div className="hz-kpi__l">{label}</div><div className={`hz-kpi__v ${cls ?? ""}`}>{value}</div>{sub && <div className="hz-muted" style={{ fontSize: 11 }}>{sub}</div>}</div>
);
function Seg<T extends string | number | null>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="hz-filters">
      {options.map(([v, l]) => (
        <button key={String(v)} className={`hz-btn${value === v ? "" : " hz-btn--ghost"}`} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}

/* ---------- 诊断 / 研究 ---------- */
const STAT_HEAD = ["笔数", "胜率", "平均/笔", "中位", "盈亏比(PF)", "均盈/均亏", "最大亏损", "持有中"];
function StatCells({ s }: { s: HorizonStats }) {
  return (
    <>
      <td className="r hz-num">{s.n}</td>
      <td className="r hz-num">{s.win_rate == null ? "—" : `${s.win_rate.toFixed(1)}%`}</td>
      <td className={`r hz-num ${pnl(s.avg_ret)}`}>{pct(s.avg_ret)}</td>
      <td className={`r hz-num ${pnl(s.median_ret)}`}>{pct(s.median_ret)}</td>
      <td className={`r hz-num ${s.profit_factor != null && s.profit_factor < 1 ? "hz-down" : ""}`}>{num(s.profit_factor)}</td>
      <td className="r hz-num">{num(s.pl_ratio)}</td>
      <td className="r hz-num hz-down">{pct(s.max_loss)}</td>
      <td className="r hz-num">{s.holding}</td>
    </>
  );
}

export function HorizonMetricsPage({ horizon }: { horizon: H }) {
  const [origin, setOrigin] = useState<"all" | HorizonOrigin>("all");
  const [win, setWin] = useState<number | null>(null);
  const res = useApi<HorizonMetricsResponse>(() => api.horizonMetrics(horizon, origin, win), [horizon, origin, win]);
  const m = res.data;
  const o = m?.overview;
  return (
    <div className="hz">
      <section className="panel hz-pad">
        <div className="hz-sechead"><b>{hzName(horizon)} · 诊断 / 研究</b>
          <span className="hz-muted">只统计已平仓（含 0.3% 成本）；PF = 盈利总和 / 亏损总和，均盈/均亏 = 平均盈利 / 平均亏损</span></div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
          <span className="hz-muted">来源</span>
          <Seg value={origin} onChange={setOrigin} options={[["all", "全部"], ["live", "实盘"], ["replay", "回放"], ["legacy", "旧数据"]]} />
          <span className="hz-muted">信号日窗口</span>
          <Seg value={win} onChange={setWin} options={[[null, "全部"], [90, "近 90 天"], [180, "近 180 天"], [365, "近 1 年"]]} />
          {m?.date_range.first_signal && <span className="hz-muted">信号日 {m.date_range.first_signal} ~ {m.date_range.last_signal}</span>}
        </div>
      </section>
      {res.loading ? <PageSkeleton title="加载诊断数据" /> : res.error ? <ErrorPage title="诊断数据加载失败" message={res.error} onRetry={res.refetch} /> : !m || !o || o.total_picks === 0 ? (
        <section className="panel hz-pad"><EmptyState title="当前口径没有样本" reason="换个来源或时间窗口。" /></section>
      ) : (
        <>
          <section className="panel hz-pad">
            <div className="hz-sechead"><b>核心 KPI</b><span className="hz-muted">共 {o.total_picks} 只入选：已平仓 {o.n}、持有中 {o.holding}、待买入 {o.pending}、开盘涨停取消 {o.skipped}</span></div>
            <div className="hz-metrics">
              <Kpi label="胜率" value={o.win_rate == null ? "—" : `${o.win_rate.toFixed(1)}%`} sub={`${o.n} 笔已平仓`} />
              <Kpi label="盈亏比（PF）" value={num(o.profit_factor)} />
              <Kpi label="均盈 / 均亏" value={num(o.pl_ratio)} sub={`${pct(o.avg_win)} / ${pct(o.avg_loss)}`} />
              <Kpi label="平均 / 笔" value={pct(o.avg_ret)} cls={pnl(o.avg_ret)} sub={`中位 ${pct(o.median_ret)}`} />
              <Kpi label="逐笔累计" value={pct(o.total_ret, 1)} cls={pnl(o.total_ret)} />
              <Kpi label="最大单笔" value={`${pct(o.max_gain)} / ${pct(o.max_loss)}`} />
              <Kpi label="平均持有（交易日）" value={num(o.avg_days, 1)} sub={o.delayed_exit ? `跌停顺延 ${o.delayed_exit} 笔` : undefined} />
            </div>
          </section>
          {origin === "all" && (
            <section className="panel hz-pad">
              <div className="hz-sechead"><b>实盘 vs 回放</b><span className="hz-muted">回放是研究证据，实盘才是前瞻表现</span></div>
              <div className="hz-tablewrap"><table className="hz-table">
                <thead><tr><th>来源</th>{STAT_HEAD.map((h) => <th key={h} className="r">{h}</th>)}</tr></thead>
                <tbody>{(Object.keys(m.by_origin) as HorizonOrigin[]).filter((k) => m.by_origin[k].total_picks > 0).map((k) => (
                  <tr key={k}><td><Origin o={k} /></td><StatCells s={m.by_origin[k]} /></tr>))}</tbody>
              </table></div>
            </section>
          )}
          <section className="panel hz-pad">
            <div className="hz-sechead"><b>按策略</b><span className="hz-muted">PF &lt; 1 标绿 = 这段样本里亏钱</span></div>
            <div className="hz-tablewrap"><table className="hz-table">
              <thead><tr><th>策略</th><th className="r">持有期</th>{STAT_HEAD.map((h) => <th key={h} className="r">{h}</th>)}<th>信号日范围</th></tr></thead>
              <tbody>{[...m.by_strategy].sort((a, b) => (b.profit_factor ?? -1) - (a.profit_factor ?? -1)).map((s) => (
                <tr key={s.strategy_id}><td>{s.strategy_name}</td><td className="r hz-num">{s.hold_days}</td><StatCells s={s} />
                  <td className="hz-num hz-muted">{s.first_signal ? `${s.first_signal} ~ ${s.last_signal}` : "暂无出票"}</td></tr>))}</tbody>
            </table></div>
          </section>
          <div className="hz-grid">
            <section className="panel hz-pad">
              <div className="hz-sechead"><b>批内排名 Top-K</b><span className="hz-muted">只看每批排名前 K 的票；越靠前越好说明排序有效</span></div>
              <div className="hz-tablewrap"><table className="hz-table">
                <thead><tr><th>范围</th><th className="r">笔数</th><th className="r">胜率</th><th className="r">平均/笔</th><th className="r">PF</th></tr></thead>
                <tbody>{m.top_k.map((t) => (
                  <tr key={t.k}><td>前 {t.k}</td><td className="r hz-num">{t.n}</td><td className="r hz-num">{t.win_rate == null ? "—" : `${t.win_rate.toFixed(1)}%`}</td>
                    <td className={`r hz-num ${pnl(t.avg_ret)}`}>{pct(t.avg_ret)}</td><td className="r hz-num">{num(t.profit_factor)}</td></tr>))}</tbody>
              </table></div>
            </section>
            <section className="panel hz-pad">
              <div className="hz-sechead"><b>按信号月</b><span className="hz-muted">看收益是否集中在少数月份</span></div>
              <div className="hz-tablewrap"><table className="hz-table">
                <thead><tr><th>月份</th><th className="r">已平仓</th><th className="r">胜率</th><th className="r">平均/笔</th><th className="r">累计</th><th className="r">持有中</th></tr></thead>
                <tbody>{m.by_month.map((x) => (
                  <tr key={x.month}><td className="hz-num">{x.month}</td><td className="r hz-num">{x.n}</td><td className="r hz-num">{x.win_rate == null ? "—" : `${x.win_rate.toFixed(1)}%`}</td>
                    <td className={`r hz-num ${pnl(x.avg_ret)}`}>{pct(x.avg_ret)}</td><td className={`r hz-num ${pnl(x.total_ret)}`}>{pct(x.total_ret, 1)}</td><td className="r hz-num">{x.holding}</td></tr>))}</tbody>
              </table></div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

/* ---------- 交易明细 / 持仓（同一份平铺数据） ---------- */
function useTrades(horizon: H) {
  return useApi<HorizonTradesResponse>(() => api.horizonTrades(horizon), [horizon]);
}

function TradeTable({ rows, horizon, mode }: { rows: HorizonTrade[]; horizon: H; mode: "history" | "positions" }) {
  const [target, setTarget] = useState<Target | null>(null);
  return (
    <>
      {target && <PickDrawer horizon={horizon} target={target} onClose={() => setTarget(null)} onOpen={setTarget} />}
      <div className="hz-tablewrap"><table className="hz-table">
        <thead><tr>
          <th>信号日</th><th>策略</th><th>代码</th><th>名称</th><th>来源</th><th>状态</th><th>买入</th>
          <th>{mode === "positions" ? "最新估值" : "卖出 / 估值"}</th><th className="r">{mode === "positions" ? "已持 / 持有期" : "天数"}</th><th className="r">收益</th>
        </tr></thead>
        <tbody>{rows.map((p) => (
          <tr key={`${p.strategy_id}${p.signal_date}${p.code}`} className={`hz-row${p.status === "skipped" ? " hz-row--cancel" : ""}`}
            onClick={() => setTarget({ sid: p.strategy_id, date: p.signal_date, code: p.code })} title="点击查看个股详情">
            <td className="hz-num">{p.signal_date}</td><td>{p.strategy_name}</td><td className="hz-num">{p.code}</td><td>{p.name}</td>
            <td><Origin o={p.origin} /></td>
            <td><Status s={p.status} />{p.status === "skipped" || (p.exit_delayed_days ?? 0) > 0 ? <PickStatusLine p={{ ...p, total_mv: 0 }} /> : null}</td>
            <td className="hz-num">{p.entry_date ? `${p.entry_date} @${p.entry_price ?? "—"}` : "—"}</td>
            <td className="hz-num">{p.exit_date ? `${p.exit_date} @${p.exit_price ?? "—"}` : p.mark_date ? `${p.mark_date} @${p.mark_price ?? "—"}（估）` : "—"}</td>
            <td className="r hz-num">{mode === "positions" ? `${p.days ?? 0} / ${p.hold_days}` : p.days ?? "—"}</td>
            <td className={`r hz-num ${pnl(p.ret)}`}>{pct(p.ret)}{p.status === "holding" ? <span className="hz-muted">（浮动）</span> : null}</td>
          </tr>))}</tbody>
      </table></div>
    </>
  );
}

const PAGE = 50;
export function HorizonTradesPage({ horizon }: { horizon: H }) {
  const res = useTrades(horizon);
  const [sid, setSid] = useState("all");
  const [st, setSt] = useState("all");
  const [origin, setOrigin] = useState<"all" | HorizonOrigin>("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const all = res.data?.trades ?? [];
  const strategies = useMemo(() => [...new Map(all.map((t) => [t.strategy_id, t.strategy_name])).entries()], [all]);
  const kw = q.trim();
  const rows = all.filter((t) => (sid === "all" || t.strategy_id === sid) && (st === "all" || t.status === st)
    && (origin === "all" || t.origin === origin) && (!kw || t.code.includes(kw) || t.name.includes(kw)));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const cur = Math.min(page, pages);
  const reset = <T,>(f: (v: T) => void) => (v: T) => { f(v); setPage(1); };
  if (res.loading) return <PageSkeleton title={`${hzName(horizon)}交易明细加载中`} />;
  if (res.error) return <ErrorPage title="交易明细加载失败" message={res.error} onRetry={res.refetch} />;
  return (
    <div className="hz">
      <section className="panel hz-pad">
        <div className="hz-sechead"><b>{hzName(horizon)} · 交易明细</b>
          <span className="hz-muted">共 {all.length} 只入选，筛选后 {rows.length} 只 · 第 {cur}/{pages} 页</span>
          <div className="hz-filters">
            <input className="hz-select" placeholder="代码 / 名称" value={q} onChange={(e) => reset(setQ)(e.target.value)} />
            <select className="hz-select" value={sid} onChange={(e) => reset(setSid)(e.target.value)}>
              <option value="all">全部策略</option>{strategies.map(([id, n]) => <option key={id} value={id}>{n}</option>)}
            </select>
            <select className="hz-select" value={st} onChange={(e) => reset(setSt)(e.target.value)}>
              <option value="all">全部状态</option>{Object.entries(STATUS).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <select className="hz-select" value={origin} onChange={(e) => reset(setOrigin)(e.target.value as typeof origin)}>
              <option value="all">全部来源</option>{(Object.keys(ORIGIN_LABEL) as HorizonOrigin[]).map((k) => <option key={k} value={k}>{ORIGIN_LABEL[k]}</option>)}
            </select>
          </div>
        </div>
        {rows.length === 0 ? <EmptyState title="没有符合条件的交易" reason="换个筛选条件。" />
          : <TradeTable rows={rows.slice((cur - 1) * PAGE, cur * PAGE)} horizon={horizon} mode="history" />}
      </section>
      <div className="hz-pager">
        <button className="hz-btn hz-btn--ghost" disabled={cur <= 1} onClick={() => setPage(cur - 1)}>上一页</button>
        <span className="hz-num">{cur} / {pages}</span>
        <button className="hz-btn hz-btn--ghost" disabled={cur >= pages} onClick={() => setPage(cur + 1)}>下一页</button>
      </div>
    </div>
  );
}

export function HorizonPositionsPage({ horizon }: { horizon: H }) {
  const res = useTrades(horizon);
  if (res.loading) return <PageSkeleton title={`${hzName(horizon)}持仓加载中`} />;
  if (res.error) return <ErrorPage title="持仓加载失败" message={res.error} onRetry={res.refetch} />;
  const all = res.data?.trades ?? [];
  const holding = all.filter((t) => t.status === "holding").sort((a, b) => (b.days ?? 0) - (a.days ?? 0));
  const pending = all.filter((t) => t.status === "pending");
  const due = holding.filter((t) => (t.days ?? 0) >= t.hold_days - 1);
  const avg = holding.length ? holding.reduce((s, t) => s + (t.ret ?? 0), 0) / holding.length : null;
  return (
    <div className="hz">
      <section className="panel hz-pad">
        <div className="hz-sechead"><b>{hzName(horizon)} · 持仓 / 待卖</b><span className="hz-muted">到第「持有期」个交易日开盘卖出；跌停封死顺延</span></div>
        <div className="hz-metrics">
          <Kpi label="持有中" value={`${holding.length} 只`} />
          <Kpi label="浮动平均" value={pct(avg)} cls={pnl(avg)} sub="按最新收盘，未扣成本" />
          <Kpi label="下一交易日到期卖出" value={`${due.length} 只`} />
          <Kpi label="待买入（下一交易日开盘）" value={`${pending.length} 只`} />
        </div>
      </section>
      {holding.length + pending.length === 0 ? <section className="panel hz-pad"><EmptyState title="当前没有持仓或待买入" reason="已平仓的在「复盘 → 交易明细」里看。" /></section> : (
        <section className="panel hz-pad"><TradeTable rows={[...pending, ...holding]} horizon={horizon} mode="positions" /></section>
      )}
    </div>
  );
}

/* ---------- 运行记录 ---------- */
const RUN_STATUS: Record<string, [string, string]> = {
  ok: ["已出票", "blue"], no_signal: ["无信号", "gray"], data_error: ["数据异常", "amber"], slots_full: ["仓位已满", "gray"], not_signal_day: ["非选股日", "gray"],
};
const DETAIL_CN: Record<string, string> = {
  slots_full: "持仓名额已满", all_held: "候选都已在持有中", small_cap_below_ma20: "小盘指数在 20 日线下", not_signal_day: "", no_data: "当天数据未就绪",
};

export function HorizonRunsPage({ horizon }: { horizon: H }) {
  const res = useApi<HorizonRunsResponse>(() => api.horizonRuns(horizon), [horizon]);
  if (res.loading) return <PageSkeleton title="运行记录加载中" />;
  if (res.error) return <ErrorPage title="运行记录加载失败" message={res.error} onRetry={res.refetch} />;
  const days = res.data?.days ?? [];
  return (
    <div className="hz">
      <section className="panel hz-pad">
        <div className="hz-sechead"><b>{hzName(horizon)} · 运行记录</b><span className="hz-muted">每天 17:35 盘后（大宗约 21:00）各策略的出票结果；只记录真实盘后运行，不含历史回放</span></div>
        {days.length === 0 ? <EmptyState title="还没有运行记录" reason="盘后流程跑过一次后这里就有记录。" /> : (
          <div className="hz-tablewrap"><table className="hz-table">
            <thead><tr><th>日期</th><th>策略</th><th>结果</th><th className="r">出票</th><th>说明</th><th>记录时间</th></tr></thead>
            <tbody>{days.flatMap((d) => d.items.map((it, i) => {
              const [l, c] = RUN_STATUS[it.status] ?? [it.status, "gray"];
              return (
                <tr key={d.date + it.strategy_id}>
                  <td className="hz-num">{i === 0 ? d.date : ""}</td><td>{it.strategy_name}</td>
                  <td><span className={`hz-pill hz-pill--${c}`}>{l}</span></td>
                  <td className="r hz-num">{it.picks ?? "—"}</td>
                  <td className="hz-muted">{it.detail ? (DETAIL_CN[it.detail] ?? it.detail) : ""}</td>
                  <td className="hz-num hz-muted">{it.recorded_at ? new Date(it.recorded_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</td>
                </tr>);
            }))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}
