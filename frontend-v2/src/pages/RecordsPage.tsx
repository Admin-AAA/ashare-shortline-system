import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ChevronRight,
  LayoutGrid,
  ListChecks,
  PlayCircle,
} from "lucide-react";
import { api } from "../api/client";
import type {
  MatrixRecordDetail,
  MatrixRecordsResponse,
  RecommendationDetail,
  RecommendationOutcome,
  RecommendationReturns,
  RunRecordDetail,
  RunSummary,
} from "../types";
import { ErrorPage, EmptyState, PageSkeleton } from "../components/StateViews";
import { RecommendationDetailCard } from "../components/RecommendationDetailCard";
import { fmtPrice } from "../components/Shared";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";

/**
 * 记录中心 —— 两类历史记录的统一复盘入口：
 *  1. 运行推荐记录（RecommendationRun）：每次跑管线产出的候选
 *  2. 矩阵运行记录（StockMatrixHistory）：单股票矩阵分析/回测
 *
 * 点进某条记录 → 当次推荐的股票 + 1/3/5日 + 至今收益率。
 *
 * 路由：
 *  - /records            列表（tab 切换）
 *  - /records?tab=run&run_id=xxx    推荐 run 详情
 *  - /records?tab=matrix&matrix_id=xxx  矩阵记录详情
 */
export function RecordsPage() {
  const { navigate } = useUrlRoute();
  const tab = (queryParam("tab") as "run" | "matrix") || "run";
  const runId = queryParam("run_id");
  const recId = queryParam("rec_id");
  const matrixId = queryParam("matrix_id");

  // 路由身份：任一参数变化 → key 变化 → 整段内容强制重挂载，
  // 彻底规避「URL 变了但父组件没重渲染 / 内部 state 残留」的问题。
  const routeKey = `${tab}|${runId ?? ""}|${recId ?? ""}|${matrixId ?? ""}`;

  let content: React.ReactNode;
  if (tab === "run" && runId && recId) {
    content = (
      <StockAnalysisDetailView
        recId={recId}
        onBack={() => navigate("/records", { params: { tab: "run", run_id: runId } })}
      />
    );
  } else if (tab === "run" && runId) {
    content = (
      <RunRecordDetailView
        runId={runId}
        onBack={() => navigate("/records", { params: { tab: "run" } })}
        onOpenStock={(recId) =>
          navigate("/records", { params: { tab: "run", run_id: runId, rec_id: recId } })
        }
      />
    );
  } else if (tab === "matrix" && matrixId) {
    content = (
      <MatrixRecordDetailView
        recordId={Number(matrixId)}
        onBack={() => navigate("/records", { params: { tab: "matrix" } })}
      />
    );
  } else {
    content = <RecordsListPage initialTab={tab} />;
  }

  return <div key={routeKey}>{content}</div>;
}

// ============================================================
// 列表页
// ============================================================

function RecordsListPage({ initialTab }: { initialTab: "run" | "matrix" }) {
  const { navigate } = useUrlRoute();
  const [tab, setTab] = useState<"run" | "matrix">(initialTab);

  // 同步 URL tab
  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  const switchTab = (next: "run" | "matrix") => {
    setTab(next);
    navigate("/records", { replace: true, params: { tab: next } });
  };

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">记录中心</span>
        <h2 className="panel__title">运行记录复盘</h2>
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-base)" }}>
          查看历史运行与推荐股票的收益表现
        </span>
      </div>

      {/* Tab 切换 */}
      <div style={{ display: "flex", gap: "var(--space-2)", borderBottom: "1px solid var(--border)", marginBottom: "var(--space-4)" }}>
        <TabButton active={tab === "run"} onClick={() => switchTab("run")} icon={<ListChecks size={14} />}>
          运行推荐记录
        </TabButton>
        <TabButton active={tab === "matrix"} onClick={() => switchTab("matrix")} icon={<LayoutGrid size={14} />}>
          矩阵运行记录
        </TabButton>
      </div>

      {tab === "run" ? <RunRecordsList /> : <MatrixRecordsList />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-2) var(--space-4)",
        background: "transparent",
        border: "none",
        borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
        color: active ? "var(--text)" : "var(--muted)",
        cursor: "pointer",
        fontSize: "var(--text-base)",
        fontWeight: active ? 600 : 400,
        marginBottom: "-1px",
      }}
    >
      {icon}
      {children}
    </button>
  );
}

// ============================================================
// 运行推荐记录列表
// ============================================================

function RunRecordsList() {
  const { navigate } = useUrlRoute();
  const { data, loading, error } = useApi<RunSummary[]>(
    (_signal) => api.runRecords({ limit: 100 }),
    [],
  );

  if (loading) return <PageSkeleton title="运行推荐记录" hint="加载历史运行" rowCount={8} />;
  if (error) return <ErrorPage title="记录加载失败" message={error} onRetry={() => window.location.reload()} />;
  if (!data || data.length === 0) {
    return <EmptyState icon={<PlayCircle size={32} />} title="暂无运行记录" reason="还没有跑过推荐管线" />;
  }

  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thStyle}>日期</th>
          <th style={thStyle}>运行名</th>
          <th style={{ ...thStyle, textAlign: "right" }}>推荐数</th>
          <th style={{ ...thStyle, textAlign: "right" }}>分数线</th>
          <th style={{ ...thStyle, textAlign: "right" }}>一日收益</th>
          <th style={{ ...thStyle, textAlign: "right" }}>三日收益</th>
          <th style={{ ...thStyle, textAlign: "right" }}>五日收益</th>
          <th style={thStyle}>状态</th>
          <th style={thStyle}>完成时间</th>
          <th style={thStyle}></th>
        </tr>
      </thead>
      <tbody>
        {data.map((run) => (
          <tr
            key={run.run_id}
            onClick={() => navigate("/records", { params: { tab: "run", run_id: run.run_id } })}
            style={{ ...rowStyle, cursor: "pointer" }}
            title="点击查看当次推荐的股票"
          >
            <td style={tdStyle} className="text-mono">{run.trade_date}</td>
            <td style={tdStyle}>{run.run_name || run.run_id.slice(0, 12)}</td>
            <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>{run.recommended_count}</td>
            <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>{run.score_line.toFixed(1)}</td>
            <ReturnCell value={run.returns?.return_1d ?? null} />
            <ReturnCell value={run.returns?.return_3d ?? null} />
            <ReturnCell value={run.returns?.return_5d ?? null} />
            <td style={tdStyle}>
              <StatusBadge status={run.status} />
            </td>
            <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
              {run.finished_at ? formatTime(run.finished_at) : "—"}
            </td>
            <td style={{ ...tdStyle, textAlign: "right" }}>
              <ChevronRight size={14} style={{ color: "var(--muted)" }} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "succeeded" || status === "completed"
      ? "var(--market-up)"
      : status === "running"
        ? "var(--accent)"
        : status === "failed" || status === "interrupted"
          ? "var(--market-down)"
          : "var(--muted)";
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 4,
      fontSize: "var(--text-sm)",
      color,
      background: `color-mix(in srgb, ${color} 12%, transparent)`,
    }}>
      {status}
    </span>
  );
}

// ============================================================
// 矩阵运行记录列表
// ============================================================

function MatrixRecordsList() {
  const { navigate } = useUrlRoute();
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const { data, loading, error } = useApi<MatrixRecordsResponse>(
    () => api.matrixRecords({ page, page_size: pageSize }),
    [page],
  );

  if (loading) return <PageSkeleton title="矩阵运行记录" hint="加载矩阵分析历史" rowCount={8} />;
  if (error) return <ErrorPage title="记录加载失败" message={error} onRetry={() => window.location.reload()} />;
  if (!data || data.items.length === 0) {
    return <EmptyState icon={<LayoutGrid size={32} />} title="暂无矩阵记录" reason="还没有跑过单股票矩阵分析" />;
  }

  const totalPages = Math.max(1, Math.ceil(data.total / pageSize));

  return (
    <>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>分析时间</th>
            <th style={thStyle}>股票</th>
            <th style={thStyle}>分析日</th>
            <th style={thStyle}>类型</th>
            <th style={{ ...thStyle, textAlign: "right" }}>评分</th>
            <th style={{ ...thStyle, textAlign: "right" }}>预估胜率</th>
            <th style={{ ...thStyle, textAlign: "right" }}>skill数</th>
            <th style={thStyle}></th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((item) => (
            <tr
              key={item.id}
              onClick={() => navigate("/records", { params: { tab: "matrix", matrix_id: String(item.id) } })}
              style={{ ...rowStyle, cursor: "pointer" }}
              title="点击查看分析详情与收益"
            >
              <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                {formatTime(item.created_at)}
              </td>
              <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>{item.code}</td>
              <td style={tdStyle} className="text-mono">{item.trade_date}</td>
              <td style={tdStyle}>
                <span style={{ color: item.analysis_type === "backtest" ? "var(--accent)" : "var(--muted)" }}>
                  {item.analysis_type}
                </span>
              </td>
              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                {item.final_score != null ? item.final_score.toFixed(1) : "—"}
              </td>
              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                {item.win_rate_est != null ? `${(item.win_rate_est * 100).toFixed(1)}%` : "—"}
              </td>
              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>{item.skill_count}</td>
              <td style={{ ...tdStyle, textAlign: "right" }}>
                <ChevronRight size={14} style={{ color: "var(--muted)" }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-3) 0", borderTop: "1px solid var(--border)" }}>
        <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} style={pageBtnStyle(page === 1)}>
          上一页
        </button>
        <span style={{ fontSize: "var(--text-base)", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
          {page} / {totalPages} · 共 {data.total} 条
        </span>
        <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages} style={pageBtnStyle(page >= totalPages)}>
          下一页
        </button>
      </div>
    </>
  );
}

// ============================================================
// 推荐运行详情（当次推荐的股票 + 收益率）
// ============================================================

function RunRecordDetailView({ runId, onBack, onOpenStock }: { runId: string; onBack: () => void; onOpenStock?: (recId: string) => void }) {
  const { data, loading, error } = useApi<RunRecordDetail>(() => api.runRecordDetail(runId), [runId]);

  if (loading) return <PageSkeleton title="运行详情" hint="加载推荐股票与收益率" rowCount={10} />;
  if (error) return <ErrorPage title="详情加载失败" message={error} onRetry={onBack} />;
  if (!data) return null;

  const { run, recommendations, as_of_date } = data;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <button className="btn btn--ghost" onClick={onBack} style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={14} />
        返回记录列表
      </button>

      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">推荐运行详情</div>
            <h3 className="panel__title">{run.run_name ?? run.run_id}</h3>
          </div>
          <StatusBadge status={run.status} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "var(--space-3)" }}>
          <Metric label="推荐日" value={run.trade_date} mono />
          <Metric label="推荐数" value={String(run.recommended_count)} mono />
          <Metric label="分数线" value={run.score_line.toFixed(1)} mono />
          <Metric label="预设" value={run.preset_name || "—"} />
          <Metric label="完成时间" value={run.finished_at ? formatTime(run.finished_at) : "—"} />
          <Metric label="收益基准日" value={as_of_date || "—"} mono />
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">推荐股票</div>
            <h3 className="panel__title">收益表现</h3>
          </div>
          <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
            基准价 = recommend_price · 收益率用 pct_chg 链式累乘（除权安全）
          </span>
        </div>

        {recommendations.length === 0 ? (
          <EmptyState title="该运行无推荐股票" reason="可能未到分数线或全被过滤" />
        ) : (
          <RecommendationsReturnTable recommendations={recommendations} onOpenStock={onOpenStock} />
        )}
      </section>
    </div>
  );
}

function RecommendationsReturnTable({
  recommendations,
  onOpenStock,
}: {
  recommendations: RunRecordDetail["recommendations"];
  onOpenStock?: (recId: string) => void;
}) {
  // 汇总统计
  const stats = computeStats(recommendations);
  const clickable = !!onOpenStock;

  return (
    <>
      <div style={{ display: "flex", gap: "var(--space-4)", padding: "var(--space-3) 0", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
        <ReturnStat label="至今均值" value={stats.avg_to_date} />
        <ReturnStat label="至今胜率" value={stats.win_rate_to_date} suffix="%" />
        <ReturnStat label="1日均值" value={stats.avg_1d} />
        <ReturnStat label="3日均值" value={stats.avg_3d} />
        <ReturnStat label="5日均值" value={stats.avg_5d} />
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thStyle}>代码</th>
              <th style={thStyle}>名称</th>
              <th style={{ ...thStyle, textAlign: "right" }}>推荐价</th>
              <th style={{ ...thStyle, textAlign: "right" }}>分数</th>
              <th style={thStyle}>策略/买点</th>
              <th style={{ ...thStyle, textAlign: "right" }}>1日</th>
              <th style={{ ...thStyle, textAlign: "right" }}>3日</th>
              <th style={{ ...thStyle, textAlign: "right" }}>5日</th>
              <th style={{ ...thStyle, textAlign: "right" }}>至今</th>
              <th style={thStyle}>最新日</th>
              {clickable && <th style={{ ...thStyle, width: 36 }} aria-hidden />}
            </tr>
          </thead>
          <tbody>
            {recommendations.map((rec) => {
              const r = rec.returns;
              return (
                <tr
                  key={rec.recommendation_id}
                  className={clickable ? "records-row--clickable" : undefined}
                  onClick={clickable ? () => onOpenStock?.(rec.recommendation_id) : undefined}
                  title={clickable ? "点击查看该股票的分析记录" : undefined}
                >
                  <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>{rec.code}</td>
                  <td style={tdStyle}>
                    {rec.name}
                    <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginLeft: 6 }}>{rec.industry || ""}</span>
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {fmtPrice(rec.recommend_price)}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {rec.final_score.toFixed(1)}
                  </td>
                  <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span style={{ fontFamily: "var(--font-mono)" }}>{rec.bucket || "—"}</span>
                      <span>{rec.setup_type || "—"}</span>
                    </div>
                  </td>
                  <ReturnCell value={r.return_1d} />
                  <ReturnCell value={r.return_3d} />
                  <ReturnCell value={r.return_5d} />
                  <ReturnCell value={r.return_to_date} bold />
                  <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }} className="text-mono">
                    {r.latest_date || "—"}
                  </td>
                  {clickable && (
                    <td style={{ ...tdStyle, textAlign: "right" }}>
                      <ChevronRight size={14} className="records-row__chevron" style={{ color: "var(--muted)" }} />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ReturnCell({ value, bold }: { value: number | null; bold?: boolean }) {
  const color = value == null ? "var(--muted)" : value >= 0 ? "var(--market-up)" : "var(--market-down)";
  return (
    <td style={{
      ...tdStyle,
      textAlign: "right",
      fontFamily: "var(--font-mono)",
      color,
      fontWeight: bold ? 600 : 400,
    }}>
      {value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`}
    </td>
  );
}

function ReturnStat({ label, value, suffix }: { label: string; value: number | null; suffix?: string }) {
  const color = value == null ? "var(--muted)" : value >= 0 ? "var(--market-up)" : "var(--market-down)";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", color, fontWeight: 600 }}>
        {value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}${suffix || "%"}`}
      </span>
    </div>
  );
}

function computeStats(recs: RunRecordDetail["recommendations"]) {
  const pick = (r: RecommendationReturns | undefined, key: keyof RecommendationReturns) =>
    r && typeof r[key] === "number" ? (r[key] as number) : null;

  const avg = (vals: (number | null)[]) => {
    const valid = vals.filter((v): v is number => v != null);
    return valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
  };
  const winRate = (vals: (number | null)[]) => {
    const valid = vals.filter((v): v is number => v != null);
    return valid.length ? (valid.filter((v) => v > 0).length / valid.length) * 100 : null;
  };

  const toDates = recs.map((r) => pick(r.returns, "return_to_date"));
  const d1 = recs.map((r) => pick(r.returns, "return_1d"));
  const d3 = recs.map((r) => pick(r.returns, "return_3d"));
  const d5 = recs.map((r) => pick(r.returns, "return_5d"));

  return {
    avg_to_date: avg(toDates),
    win_rate_to_date: winRate(toDates),
    avg_1d: avg(d1),
    avg_3d: avg(d3),
    avg_5d: avg(d5),
  };
}

// ============================================================
// 单股票分析记录（从运行详情下钻）
// ============================================================

function StockAnalysisDetailView({ recId, onBack }: { recId: string; onBack: () => void }) {
  const { data, loading, error } = useApi<RecommendationDetail>(
    () => api.recommendationDetail(recId),
    [recId],
  );

  if (loading) return <PageSkeleton title="股票分析记录" hint="加载决策详情与收益复盘" rowCount={10} />;
  if (error) return <ErrorPage title="分析记录加载失败" message={error} onRetry={onBack} />;
  if (!data) return null;

  const { outcome, timeline, score_breakdown, review_note, feature_snapshot } = data;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <button className="btn btn--ghost" onClick={onBack} style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={14} />
        返回运行详情
      </button>

      {/* 头部：代码 / 名称 / 分数 / 关键价 */}
      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">推荐股票分析</div>
            <h3 className="panel__title" style={{ fontFamily: "var(--font-mono)" }}>
              {data.code}
              <span style={{ fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif", marginLeft: "var(--space-3)", color: "var(--ink)" }}>
                {data.name}
              </span>
            </h3>
          </div>
          {data.status && <StatusBadge status={data.status} />}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "var(--space-3)" }}>
          <Metric label="推荐日" value={data.trade_date} mono />
          <Metric label="分数" value={data.final_score.toFixed(1)} mono />
          <Metric label="共识分" value={data.consensus_score.toFixed(1)} mono />
          <Metric label="推荐价" value={fmtPrice(data.recommend_price)} mono />
          <Metric label="策略/买点" value={`${data.bucket || "—"} · ${data.setup_type || "—"}`} />
          <Metric label="风险等级" value={data.risk_tags?.[0] || "—"} />
        </div>
      </section>

      {/* 决策分析（交易参数 + 买点原因 + 评分分解 + 风险标签） */}
      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">决策分析</div>
            <h3 className="panel__title">为什么推荐它</h3>
          </div>
        </div>
        <RecommendationDetailCard item={data} />
      </section>

      {/* 技术快照 */}
      {feature_snapshot && Object.keys(feature_snapshot).length > 0 && (
        <FeatureSnapshotSection snapshot={feature_snapshot} />
      )}

      {/* 收益与复盘 */}
      {(outcome || (timeline && timeline.length > 0)) && (
        <OutcomeSection outcome={outcome} timeline={timeline} />
      )}

      {/* 评分构成 */}
      {score_breakdown && Object.keys(score_breakdown).length > 0 && (
        <ScoreBreakdownSection breakdown={score_breakdown} />
      )}

      {/* 复盘笔记 */}
      {review_note && (review_note.user_label || review_note.failure_reason || review_note.review_text) && (
        <ReviewNoteSection note={review_note} />
      )}
    </div>
  );
}

function FeatureSnapshotSection({ snapshot }: { snapshot: Record<string, unknown> }) {
  const num = (v: unknown) => (typeof v === "number" && !Number.isNaN(v) ? v.toFixed(2) : "—");
  const pct = (v: unknown) => (typeof v === "number" && !Number.isNaN(v) ? `${v.toFixed(2)}%` : "—");

  const rows: Array<[string, string]> = [
    ["现价", num(snapshot.price)],
    ["MA5 / MA10 / MA20", `${num(snapshot.ma5)} / ${num(snapshot.ma10)} / ${num(snapshot.ma20)}`],
    ["趋势状态", String(snapshot.trend_status ?? "—")],
    ["MACD", `${String(snapshot.macd_status ?? "—")} (${num(snapshot.macd_dif)}/${num(snapshot.macd_dea)})`],
    ["RSI(12)", num(snapshot.rsi_12)],
    ["量比(5日)", num(snapshot.volume_ratio_5d)],
    ["支撑位", Array.isArray(snapshot.support_levels) ? (snapshot.support_levels as number[]).map((x) => x.toFixed(2)).join(" / ") || "—" : "—"],
    ["阻力位", Array.isArray(snapshot.resistance_levels) ? (snapshot.resistance_levels as number[]).map((x) => x.toFixed(2)).join(" / ") || "—" : "—"],
  ];

  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">技术快照</div>
          <h3 className="panel__title">推荐日技术面</h3>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-3)" }}>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{label}</span>
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function OutcomeSection({
  outcome,
  timeline,
}: {
  outcome: RecommendationOutcome | null;
  timeline: Array<{ label: string; trade_date: string; close_return: number; max_return: number; max_drawdown: number }>;
}) {
  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">收益与复盘</div>
          <h3 className="panel__title">分析日 → 今天</h3>
        </div>
        {outcome?.outcome_label && (
          <span style={{
            padding: "2px 8px",
            borderRadius: 4,
            fontSize: "var(--text-sm)",
            color: outcome.hit_target ? "var(--market-up)" : "var(--market-down)",
            background: `color-mix(in srgb, ${outcome.hit_target ? "var(--market-up)" : "var(--market-down)"} 12%, transparent)`,
          }}>
            {outcome.outcome_label}
          </span>
        )}
      </div>

      {outcome && (
        <div style={{ display: "flex", gap: "var(--space-5)", flexWrap: "wrap", padding: "var(--space-3) 0", borderBottom: "1px solid var(--border)" }}>
          <ReturnStat label="收盘收益" value={outcome.close_return} />
          <ReturnStat label="最大收益" value={outcome.max_return} />
          <ReturnStat label="最大回撤" value={outcome.max_drawdown} />
          {outcome.holding_days != null && <ReturnStat label="持仓天数" value={outcome.holding_days} />}
          {outcome.exit_reason && (
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>退出原因</span>
              <span style={{ fontSize: "var(--text-base)", color: "var(--text)" }}>{outcome.exit_reason}</span>
            </div>
          )}
        </div>
      )}

      {timeline && timeline.length > 0 && (
        <div style={{ overflowX: "auto", marginTop: "var(--space-3)" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>阶段</th>
                <th style={thStyle}>交易日</th>
                <th style={{ ...thStyle, textAlign: "right" }}>收盘收益</th>
                <th style={{ ...thStyle, textAlign: "right" }}>最大收益</th>
                <th style={{ ...thStyle, textAlign: "right" }}>最大回撤</th>
              </tr>
            </thead>
            <tbody>
              {timeline.map((t, i) => (
                <tr key={i} style={rowStyle}>
                  <td style={tdStyle}>{t.label}</td>
                  <td style={{ ...tdStyle, fontFamily: "var(--font-mono)", color: "var(--muted)" }}>{t.trade_date}</td>
                  <ReturnCell value={t.close_return} />
                  <ReturnCell value={t.max_return} />
                  <ReturnCell value={t.max_drawdown} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ScoreBreakdownSection({ breakdown }: { breakdown: Record<string, unknown> }) {
  const weighted = breakdown.weighted_scores as Record<string, number> | undefined;
  const raw = breakdown.raw_scores as Record<string, number> | undefined;
  const entries = weighted ?? raw ?? {};
  const keys = Object.keys(entries);

  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">评分构成</div>
          <h3 className="panel__title">分数怎么来的</h3>
        </div>
      </div>
      {keys.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {keys.map((k) => {
            const v = entries[k];
            const max = Math.max(1, ...Object.values(entries).map((x) => (typeof x === "number" ? x : 0)));
            const pct = typeof v === "number" ? Math.max(0, Math.min(100, (v / max) * 100)) : 0;
            return (
              <div key={k}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--text-sm)", marginBottom: 2 }}>
                  <span style={{ color: "var(--text-muted)" }}>{k}</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{typeof v === "number" ? v.toFixed(1) : "—"}</span>
                </div>
                <div style={{ height: 6, background: "var(--surface-soft)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent)", borderRadius: 4, transition: `width var(--motion-instant)` }} />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>无评分明细</div>
      )}

      <div style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", marginTop: "var(--space-3)", fontSize: "var(--text-sm)" }}>
        {"consensus_boost" in breakdown && <span>共识加成 <b className="text-mono">{numOr(breakdown.consensus_boost)}</b></span>}
        {"quality_cap" in breakdown && <span>质量上限 <b className="text-mono">{numOr(breakdown.quality_cap)}</b></span>}
        {"final_score" in breakdown && <span>最终分 <b className="text-mono">{numOr(breakdown.final_score)}</b></span>}
      </div>
    </section>
  );
}

function ReviewNoteSection({ note }: { note: { user_label?: string | null; failure_reason?: string | null; review_text?: string | null; updated_at?: string | null } }) {
  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">复盘笔记</div>
          <h3 className="panel__title">人工复盘</h3>
        </div>
        {note.updated_at && <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{formatTime(note.updated_at)}</span>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", fontSize: "var(--text-sm)" }}>
        {note.user_label && (
          <div>
            <span style={{ color: "var(--text-muted)" }}>结论 · </span>
            <span style={{ color: "var(--ink-secondary)" }}>{note.user_label}</span>
          </div>
        )}
        {note.failure_reason && (
          <div>
            <span style={{ color: "var(--text-muted)" }}>失败原因 · </span>
            <span style={{ color: "var(--ink-secondary)" }}>{note.failure_reason}</span>
          </div>
        )}
        {note.review_text && (
          <p style={{ margin: 0, color: "var(--ink-secondary)", lineHeight: 1.6 }}>{note.review_text}</p>
        )}
      </div>
    </section>
  );
}

const numOr = (v: unknown) =>
  typeof v === "number" && !Number.isNaN(v) ? v.toFixed(1) : "—";

// ============================================================
// 矩阵记录详情
// ============================================================

function MatrixRecordDetailView({ recordId, onBack }: { recordId: number; onBack: () => void }) {
  const { data, loading, error } = useApi<MatrixRecordDetail>(() => api.matrixRecordDetail(recordId), [recordId]);

  if (loading) return <PageSkeleton title="矩阵记录详情" hint="加载分析结果与收益" rowCount={8} />;
  if (error) return <ErrorPage title="详情加载失败" message={error} onRetry={onBack} />;
  if (!data) return null;

  const r = data.returns;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <button className="btn btn--ghost" onClick={onBack} style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={14} />
        返回记录列表
      </button>

      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">矩阵分析详情</div>
            <h3 className="panel__title" style={{ fontFamily: "var(--font-mono)" }}>
              {data.code} · {data.trade_date}
            </h3>
          </div>
          <span style={{ color: data.analysis_type === "backtest" ? "var(--accent)" : "var(--muted)" }}>
            {data.analysis_type}
          </span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "var(--space-3)" }}>
          <Metric label="基准价" value={fmtPrice(data.base_price)} mono />
          <Metric label="综合评分" value={data.final_score != null ? data.final_score.toFixed(1) : "—"} mono />
          <Metric label="预估胜率" value={data.win_rate_est != null ? `${(data.win_rate_est * 100).toFixed(1)}%` : "—"} mono />
          <Metric label="预估收益" value={data.avg_return_est != null ? `${(data.avg_return_est * 100).toFixed(2)}%` : "—"} mono />
          <Metric label="skill数" value={String(data.skill_count)} mono />
          <Metric label="耗时" value={data.elapsed_ms != null ? `${(data.elapsed_ms / 1000).toFixed(1)}s` : "—"} />
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <div>
            <div className="panel__kicker">实际收益</div>
            <h3 className="panel__title">分析日 → 今天</h3>
          </div>
          <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
            基准 = 分析日收盘价 · 至 {r.as_of_date || "—"}
          </span>
        </div>

        <div style={{ display: "flex", gap: "var(--space-5)", flexWrap: "wrap", padding: "var(--space-3) 0" }}>
          <ReturnStat label="1日" value={r.return_1d} />
          <ReturnStat label="3日" value={r.return_3d} />
          <ReturnStat label="5日" value={r.return_5d} />
          <ReturnStat label="至今" value={r.return_to_date} />
        </div>
        <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
          最新交易日：<span className="text-mono">{r.latest_date || "—"}</span>
          {r.latest_close != null && <> · 收盘 <span className="text-mono">{fmtPrice(r.latest_close)}</span></>}
        </div>
      </section>

      {data.result && Object.keys(data.result).length > 0 && (
        <section className="panel">
          <div className="panel__header">
            <div>
              <div className="panel__kicker">完整分析结果</div>
              <h3 className="panel__title">原始 JSON</h3>
            </div>
          </div>
          <pre style={{
            background: "var(--surface-soft)",
            padding: "var(--space-3)",
            borderRadius: 6,
            fontSize: "var(--text-sm)",
            fontFamily: "var(--font-mono)",
            overflow: "auto",
            maxHeight: 400,
            color: "var(--text)",
          }}>
            {JSON.stringify(data.result, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
}

// ============================================================
// 通用小组件
// ============================================================

function Metric({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{label}</span>
      <span style={{ fontFamily: mono ? "var(--font-mono)" : "inherit", color: "var(--text)" }}>{value}</span>
    </div>
  );
}

const formatTime = (iso: string | null | undefined) => {
  if (!iso) return "—";
  // ISO → MM-DD HH:MM
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "var(--text-base)",
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "var(--space-2) var(--space-3)",
  color: "var(--muted)",
  fontWeight: 500,
  fontSize: "var(--text-sm)",
  borderBottom: "1px solid var(--border)",
};

const tdStyle: React.CSSProperties = {
  padding: "var(--space-2) var(--space-3)",
  borderBottom: "1px solid var(--border)",
};

const rowStyle: React.CSSProperties = {
  cursor: "default",
};

const pageBtnStyle = (disabled: boolean): React.CSSProperties => ({
  background: "var(--surface-soft)",
  color: disabled ? "var(--muted)" : "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "6px 12px",
  fontSize: "var(--text-base)",
  cursor: disabled ? "default" : "pointer",
  opacity: disabled ? 0.5 : 1,
});
