import { Fragment, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { RecommendationHistoryResponse } from "../types";
import { exitReasonLabel, formatApiDateTime, isOutcomeSettled, setupLabel } from "../utils/display";
import { SHORT_STRATEGIES, strategyShortName } from "../ui/strategyRules";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";

/** 复盘 · 按批次（默认视图）
 * 数据：GET /api/recommendations/history（含 execution_state / cancel_observation / outcome / summary.cancel_observation）。
 * 一个批次 = 一次运行（run_id）。命中策略详情（skill_breakdown）在展开个股时按需拉取 GET /api/recommendations/{id}。 */
type Item = RecommendationHistoryResponse["items"][number];
type Status = { key: string; label: string; tone: "brand" | "hz" | "gray" | "plain"; reason?: string | null };

function statusOf(it: Item): Status {
  const ex = it.execution_state;
  const o = it.outcome;
  if (ex?.cancelled_before_entry || ex?.latest_decision_code === "cancel" || o?.exit_reason === "execution_cancelled_before_entry")
    return { key: "cancel", label: "取消买入", tone: "gray", reason: ex?.first_cancel_reason || ex?.latest_reason || "盘中确认取消" };
  if (ex?.latest_decision_code === "expired") return { key: "expired", label: "已过期", tone: "gray", reason: ex.latest_reason || "确认窗口结束仍未满足买入条件" };
  if (o?.entry_triggered) {
    if (isOutcomeSettled(o.outcome_label) || o.exit_date) return { key: "closed", label: "已平仓", tone: "plain", reason: exitReasonLabel(o.exit_reason, "—", o.executed_return) };
    return { key: "holding", label: "持有中", tone: "hz" };
  }
  if (ex?.latest_decision_code === "wait") return { key: "wait", label: "等待确认", tone: "plain", reason: ex.latest_reason };
  if (ex?.latest_decision_code === "buy" || ex?.ever_buy_confirmed) return { key: "bought", label: "已买入", tone: "brand" };
  if (o && !o.entry_triggered) return { key: "untriggered", label: "未触发", tone: "gray", reason: "买点未触发" };
  return { key: "pending", label: "待确认", tone: "plain" };
}

const pct = (v?: number | null) => (v == null ? <span className="muted">—</span> : <span className={`num ${v > 0 ? "up" : v < 0 ? "down" : ""}`}>{v > 0 ? "+" : ""}{v.toFixed(2)}%</span>);
const RESULTS = ["全部", "已买入", "持有中", "已平仓", "盈利", "亏损", "取消买入", "未触发", "已过期"];
const PER = 5;

export function BatchReviewPage() {
  const [scope, setScope] = useState<"live" | "replay">("live");
  const [code, setCode] = useState("");
  const [strategy, setStrategy] = useState("");
  const [result, setResult] = useState("全部");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Set<string> | null>(null);
  const [openStock, setOpenStock] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["batch-review", code, strategy],
    queryFn: () => api.recommendationHistory({ code: code || undefined, strategy_id: strategy || undefined, limit: 200 }),
  });

  const batches = useMemo(() => {
    const items = (q.data?.items ?? []).filter((it) => (scope === "live" ? it.evidence_class !== "historical_reconstruction" : it.evidence_class === "historical_reconstruction"));
    const match = (it: Item) => {
      const s = statusOf(it);
      const r = it.outcome?.executed_return;
      switch (result) {
        case "已买入": return ["bought", "holding", "closed"].includes(s.key);
        case "持有中": return s.key === "holding";
        case "已平仓": return s.key === "closed";
        case "盈利": return s.key === "closed" && (r ?? 0) > 0;
        case "亏损": return s.key === "closed" && (r ?? 0) <= 0;
        case "取消买入": return s.key === "cancel";
        case "未触发": return s.key === "untriggered";
        case "已过期": return s.key === "expired";
        default: return true;
      }
    };
    const map = new Map<string, { run: string; date: string; at?: string | null; preset?: string | null; all: Item[]; view: Item[] }>();
    for (const it of items) {
      const b = map.get(it.run_id) ?? { run: it.run_id, date: it.trade_date, at: it.recommended_at, preset: (it as Item & { preset_name?: string }).preset_name, all: [], view: [] };
      b.all.push(it);
      if (match(it)) b.view.push(it);
      map.set(it.run_id, b);
    }
    return [...map.values()].filter((b) => b.view.length).sort((a, b) => (a.run < b.run ? 1 : -1));
  }, [q.data, scope, result]);

  if (q.isLoading) return <PageSkeleton title="复盘" hint="加载批次" rowCount={8} />;
  if (q.error) return <ErrorPage title="加载失败" message={(q.error as Error).message} onRetry={() => void q.refetch()} />;

  const pages = Math.max(1, Math.ceil(batches.length / PER));
  const cur = Math.min(page, pages);
  const view = batches.slice((cur - 1) * PER, cur * PER);
  const filtered = Boolean(code || strategy || result !== "全部");
  const openSet = open ?? new Set(batches.slice(0, 2).map((b) => b.run));
  const co = q.data?.summary.cancel_observation;
  const toggle = (run: string) => { const n = new Set(openSet); n.has(run) ? n.delete(run) : n.add(run); setOpen(n); };

  return (
    <div className="page">
      <div className="ph">
        <div><h1>复盘 · 按批次 {scope === "replay" && <span className="tag tag--warn">回放</span>}</h1><p>每次运行一批：买了哪些股票、命中哪个策略、为什么推荐、是否在盘中确认时取消。收益为逐笔结算（含成本）。</p></div>
        <div className="seg" title="evidence_class：live_forward / historical_reconstruction">
          <button className={scope === "live" ? "on" : ""} onClick={() => setScope("live")}>LIVE 前瞻</button>
          <button className={scope === "replay" ? "on" : ""} onClick={() => setScope("replay")}>历史重建</button>
        </div>
      </div>
      {scope === "replay" && <div className="note">历史重建口径：用当前规则回放历史行情得到的样本，标记「回放」，不代表实盘前瞻表现。</div>}
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="card stat"><div className="l">已结算笔数 · 胜率</div><div className="v">{q.data?.summary.trade_count ?? 0} · {q.data?.summary.win_rate == null ? "—" : `${q.data.summary.win_rate.toFixed(1)}%`}</div><div className="d">平均每笔 {pct(q.data?.summary.average_net_return)}</div></div>
        <div className="card stat"><div className="l">取消买入笔数</div><div className="v">{co?.cancelled_count ?? 0}</div><div className="d">不计入已结算统计</div></div>
        <div className="card stat"><div className="l">取消后第 5 日假设收益</div><div className="v">{pct(co?.average_5d_return)}</div><div className="d">已观察 {co?.observed_5d_count ?? 0} 笔</div></div>
        <div className="card stat"><div className="l">避免亏损率</div><div className="v">{co?.avoided_loss_rate_5d == null ? "—" : `${co.avoided_loss_rate_5d.toFixed(0)}%`}</div><div className="d">取消后第 5 日假设收益 ≤ 0 的比例</div></div>
      </div>
      <div className="card toolbar">
        <input className="inp" placeholder="6 位代码" value={code} onChange={(e) => { setCode(e.target.value.trim()); setPage(1); }} style={{ width: 120 }} />
        <select className="inp" value={strategy} onChange={(e) => { setStrategy(e.target.value); setPage(1); }}>
          <option value="">全部策略</option>
          {SHORT_STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.short}{s.status === "观察" ? "（观察）" : ""}</option>)}
        </select>
        <select className="inp" value={result} onChange={(e) => { setResult(e.target.value); setPage(1); }}>{RESULTS.map((r) => <option key={r}>{r}</option>)}</select>
        <button className="btn btn--ghost" onClick={() => { setCode(""); setStrategy(""); setResult("全部"); setPage(1); }}>清空筛选</button>
        <span style={{ marginLeft: "auto" }} className="muted small">{batches.length} 批 · {batches.reduce((a, b) => a + b.view.length, 0)} 只</span>
        <button className="btn btn--ghost" onClick={() => setOpen(new Set(batches.map((b) => b.run)))}>全部展开</button>
        <button className="btn btn--ghost" onClick={() => setOpen(new Set())}>全部收起</button>
      </div>
      {!view.length && <EmptyState title="没有匹配的批次" reason="调整筛选条件试试" />}
      {view.map((b) => {
        const isOpen = filtered || openSet.has(b.run);
        const settled = b.all.filter((i) => statusOf(i).key === "closed");
        const avg = settled.length ? settled.reduce((a, i) => a + (i.outcome?.executed_return ?? 0), 0) / settled.length : null;
        const wins = settled.filter((i) => (i.outcome?.executed_return ?? 0) > 0).length;
        const nCancel = b.all.filter((i) => ["cancel", "untriggered", "expired"].includes(statusOf(i).key)).length;
        const setups = new Set(b.all.map((i) => i.setup_type).filter(Boolean));
        return (
          <div className="card batch" key={b.run}>
            <div className="batch__hd" onClick={() => toggle(b.run)}>
              {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <b className="num">{b.date}</b>
              <span className="mono small muted">{b.run}</span>
              {b.at && <span className="small muted">{formatApiDateTime(b.at, { hour: "2-digit", minute: "2-digit" })} 生成</span>}
              {b.preset && <span className="tag">{b.preset}</span>}
              <span className="tag">{b.view.length < b.all.length ? `匹配 ${b.view.length} / ` : ""}{b.all.length} 只</span>
              <span className="tag">{setups.size} 种买点</span>
              {nCancel > 0 && <span className="tag tag--gray">取消/未触发 {nCancel}</span>}
              <span style={{ marginLeft: "auto" }} className="small muted">已平仓平均</span>{pct(avg)}{scope === "replay" && avg != null && <span className="tag tag--warn">回放</span>}
              <span className="small muted">胜率</span><b className="num">{settled.length ? `${Math.round((wins / settled.length) * 100)}%` : "—"}</b>
            </div>
            {isOpen && (
              <table className="tbl">
                <thead><tr><th /><th>股票</th><th>命中策略 / 买点</th><th>推荐原因</th><th className="n">评分</th><th>执行状态</th><th>取消 / 卖出原因</th><th className="n">持有</th><th className="n">收益（含成本）</th></tr></thead>
                <tbody>
                  {b.view.map((it) => {
                    const s = statusOf(it);
                    const gray = s.tone === "gray";
                    const hyp = it.cancel_observation?.horizons?.["5d"]?.net_return_pct ?? null;
                    const reasons = (it.rationale ?? "").split(/[；;]/).map((x) => x.trim()).filter(Boolean);
                    const exp = openStock === it.recommendation_id;
                    return (
                      <Fragment key={it.recommendation_id}>
                        <tr className={gray ? "row--muted" : ""} onClick={() => setOpenStock(exp ? null : it.recommendation_id)} style={{ cursor: "pointer" }}>
                          <td>{exp ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</td>
                          <td><b style={{ fontWeight: 500 }}>{it.name}</b> <span className="mono small muted">{it.code}</span></td>
                          <td>{it.setup_type && <span className="tag tag--brand">{setupLabel(it.setup_type).label}</span>}</td>
                          <td className="reason">{reasons.length ? reasons.slice(0, 3).map((r) => <div key={r}>· {r}</div>) : <span className="muted">—</span>}</td>
                          <td className="n num">{it.final_score.toFixed(1)}</td>
                          <td><span className={`tag ${s.tone === "brand" ? "tag--brand" : s.tone === "hz" ? "tag--hz" : s.tone === "gray" ? "tag--gray" : ""}`}>{s.label}</span></td>
                          <td className="small">{s.reason ? <span className={gray ? "muted" : ""}>{s.reason}</span> : <span className="muted">—</span>}</td>
                          <td className="n">{it.outcome?.holding_days ? `${it.outcome.holding_days} 天` : "—"}</td>
                          <td className="n">
                            {s.key === "closed" || s.key === "holding" ? pct(it.outcome?.executed_return) : gray ? <span className="hyp">{pct(hyp)} <span className="tag tag--gray">假设·T+5</span></span> : <span className="muted">—</span>}
                          </td>
                        </tr>
                        {exp && <tr className="row--exp"><td /><td colSpan={8}><StockHits id={it.recommendation_id} /></td></tr>}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
      <div className="pager">
        <button className="btn btn--ghost" disabled={cur <= 1} onClick={() => setPage(cur - 1)}>上一页</button>
        <span className="num small">{cur} / {pages}</span>
        <button className="btn btn--ghost" disabled={cur >= pages} onClick={() => setPage(cur + 1)}>下一页</button>
      </div>
    </div>
  );
}

/** 展开：命中策略（skill_breakdown，≥2 显示 ×N 策略）+ 风险提示（仅供参考） */
function StockHits({ id }: { id: string }) {
  const d = useQuery({ queryKey: ["rec-detail", id], queryFn: () => api.recommendationDetail(id) });
  if (d.isLoading) return <span className="muted small">加载命中策略…</span>;
  if (!d.data) return <span className="muted small">无详情</span>;
  const sb = d.data.skill_breakdown ?? [];
  return (
    <div className="hits">
      <span className="muted small">命中策略：</span>
      {sb.length ? sb.map((s, i) => (
        <span key={s.strategy_id + i} className={`tag ${i === 0 ? "tag--brand" : ""}`} title={`${s.setup_match} · 规则分 ${s.score.toFixed(1)}${s.hard_veto ? ` · 否决：${s.veto_reason}` : ""}`}>
          {strategyShortName(s.strategy_id, s.strategy_name)} {s.score.toFixed(0)}
        </span>
      )) : <span className="muted small">—</span>}
      {sb.length >= 2 && <span className="tag tag--warn">×{sb.length} 策略</span>}
      {d.data.entry_rule && <span className="small" style={{ marginLeft: 12 }}><span className="muted">入场规则：</span>{d.data.entry_rule}</span>}
      {d.data.risk_tags?.length ? <span className="small" style={{ marginLeft: 12 }}><span className="muted">风险提示（仅供参考，不影响出票）：</span>{d.data.risk_tags.join("、")}</span> : null}
    </div>
  );
}
