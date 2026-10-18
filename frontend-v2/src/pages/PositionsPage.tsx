import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ExecutionConfirmationItem, ExecutionConfirmationResponse, RealtimePnlItem } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { useUrlRoute } from "../app/useUrlRoute";
import { formatBj } from "../utils/time";

/**
 * 今日 · 持仓 / 待卖 —— 实时盈亏（/api/recommendations/realtime-pnl）+ 盘中确认（批量 execution-confirmations，不调用 LLM）。
 * 持仓里盘中确认给出 减仓/离场 的即为「待卖」。
 */
const SELL = new Set(["reduce", "exit"]);
const pctCls = (v?: number | null) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
const fmtPct = (v?: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);

export function PositionsPage() {
  const { navigate } = useUrlRoute();
  const [conf, setConf] = useState<ExecutionConfirmationResponse | null>(null);
  const pnl = useQuery({ queryKey: ["realtime-pnl"], queryFn: () => api.recommendationRealtimePnl(false), refetchInterval: 60_000 });
  const names = useQuery({ queryKey: ["history-names"], queryFn: () => api.recommendationHistory({ limit: 200 }), staleTime: 300_000 });
  const nameOf = useMemo(() => {
    const m: Record<string, { name?: string; setup?: string }> = {};
    for (const it of names.data?.items ?? []) m[it.recommendation_id] = { name: it.name, setup: it.setup_type ?? undefined };
    return m;
  }, [names.data]);
  const confirm = useMutation({
    mutationFn: (ids: string[]) => api.confirmExecutionBatch(ids, true),
    onSuccess: (r) => setConf(r),
  });
  const confMap = useMemo(() => Object.fromEntries((conf?.items ?? []).map((i) => [i.recommendation_id, i])) as Record<string, ExecutionConfirmationItem>, [conf]);

  if (pnl.isLoading) return <PageSkeleton title="持仓 / 待卖" />;
  if (pnl.error) return <ErrorPage title="实时持仓加载失败" message={(pnl.error as Error).message} onRetry={() => pnl.refetch()} />;
  const items = pnl.data?.items ?? [];
  const holding = items.filter((i) => i.status === "holding");
  const toSell = holding.filter((i) => SELL.has(confMap[i.recommendation_id]?.holding_decision?.code ?? ""));
  const pending = items.filter((i) => i.status !== "holding");
  const avg = holding.length ? holding.reduce((s, i) => s + (i.pnl_pct ?? 0), 0) / holding.length : null;

  const row = (i: RealtimePnlItem, kind: "hold" | "wait") => {
    const c = confMap[i.recommendation_id];
    const d = kind === "hold" ? c?.holding_decision : c?.entry_decision;
    const sell = SELL.has(d?.code ?? "");
    return (
      <tr key={i.recommendation_id} onClick={() => navigate("/history", { params: { code: i.code } })} style={{ cursor: "pointer" }}>
        <td><b>{nameOf[i.recommendation_id]?.name ?? "—"}</b> <span className="num muted">{i.code}</span></td>
        <td className="num muted">{i.trade_date}</td>
        {kind === "hold" ? (<>
          <td className="n num">{i.entry_price?.toFixed(2) ?? "—"}</td>
          <td className="n num">{i.now_price?.toFixed(2) ?? "—"}</td>
          <td className={`n num ${pctCls(i.pnl_pct)}`}>{fmtPct(i.pnl_pct)}</td>
        </>) : (<>
          <td className="n num">{i.recommend_price?.toFixed(2) ?? "—"}</td>
          <td className="n num">{i.nearest_buy_target?.toFixed(2) ?? "—"}</td>
          <td className="n num">{i.distance_to_target_pct == null ? "—" : fmtPct(i.distance_to_target_pct)}</td>
        </>)}
        <td>{d ? <span className={`tag ${sell ? "tag--warn" : d.code === "buy" ? "tag--brand" : "tag--gray"}`}>{d.label}</span> : <span className="muted small">{i.status === "waiting" ? "待开盘" : i.status === "watching" ? "未触发买点" : i.status === "unavailable" ? "行情不可用" : "未确认"}</span>}</td>
        <td className="reason">{c?.message || d?.reason || i.reason || ""}</td>
      </tr>
    );
  };

  return (
    <div className="page">
      <div className="ph">
        <div><h1>持仓 / 待卖</h1><p>实时盈亏每分钟刷新；盘中确认实时校验买点与持仓条件，不调用 LLM。{pnl.data?.computed_at && ` 行情时间 ${formatBj(pnl.data.computed_at, "time-sec")}`}</p></div>
        <button className="btn btn--primary" disabled={confirm.isPending || items.length === 0} onClick={() => confirm.mutate(items.map((i) => i.recommendation_id))}>
          {confirm.isPending ? "确认中…" : `盘中确认（${items.length}）`}
        </button>
      </div>
      {conf && <div className="note">盘中确认完成 {formatBj(conf.computed_at, "time-sec")}：成功 {conf.summary.success_count} · 失败 {conf.summary.error_count} · 已过期 {conf.summary.expired_count}{conf.errors.length ? `；${conf.errors.slice(0, 3).map((e) => `${e.code} ${e.message}`).join("；")}` : ""}</div>}
      {confirm.error && <div className="note">盘中确认失败：{(confirm.error as Error).message}</div>}
      <div className="grid g4">
        <div className="card stat"><div className="l">持仓</div><div className="v">{holding.length}</div><div className="d">已入场未离场</div></div>
        <div className="card stat"><div className="l">持仓平均浮盈</div><div className={`v ${pctCls(avg)}`}>{fmtPct(avg)}</div><div className="d">未扣成本</div></div>
        <div className="card stat"><div className="l">待卖</div><div className="v">{conf ? toSell.length : "—"}</div><div className="d">{conf ? "盘中确认建议减仓/离场" : "点「盘中确认」后计算"}</div></div>
        <div className="card stat"><div className="l">待买入</div><div className="v">{pending.length}</div><div className="d">等待开盘或买点触发</div></div>
      </div>
      <section className="card">
        <div className="toolbar"><b>持仓</b>{toSell.length > 0 && <span className="tag tag--warn">待卖 {toSell.length}</span>}</div>
        {holding.length === 0 ? <EmptyState title="当前无持仓" /> : (
          <table className="tbl"><thead><tr><th>股票</th><th>推荐日</th><th className="n">入场价</th><th className="n">现价</th><th className="n">浮盈</th><th>盘中确认</th><th>原因</th></tr></thead>
            <tbody>{[...toSell, ...holding.filter((h) => !toSell.includes(h))].map((i) => row(i, "hold"))}</tbody></table>)}
      </section>
      <section className="card">
        <div className="toolbar"><b>待买入</b></div>
        {pending.length === 0 ? <EmptyState title="没有待买入的推荐" /> : (
          <table className="tbl"><thead><tr><th>股票</th><th>推荐日</th><th className="n">推荐价</th><th className="n">最近买点</th><th className="n">距买点</th><th>盘中确认</th><th>原因</th></tr></thead>
            <tbody>{pending.map((i) => row(i, "wait"))}</tbody></table>)}
      </section>
    </div>
  );
}
