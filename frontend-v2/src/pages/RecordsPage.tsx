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
  MatrixRecordsResponse,
  RecommendationDetail,
  RecommendationOutcome,
  RecommendationReturns,
  RunRecordDetail,
  RunSummary,
} from "../types";
import { ErrorPage, EmptyState, PageSkeleton } from "../components/StateViews";
import { RecommendationDetailCard } from "../components/RecommendationDetailCard";
import { MatrixRecordDetailView } from "../components/MatrixRecordDetailView";
import { RunModeBadge } from "../components/RunModeBadge";
import {
  Metric,
  ReturnCell,
  ReturnStat,
  fmtPrice,
  formatTime,
  tableStyle,
  thStyle,
  tdStyle,
  rowStyle,
  pageBtnStyle,
  StatusBadge,
} from "../components/recordsShared";
import { StockAnalysisDetailView } from "../components/RecordsStockDetail";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";
import { exitReasonLabel } from "../utils/display";

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
            <td style={tdStyle} className="text-mono">
              <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
                {run.trade_date}
                <RunModeBadge runMode={run.run_mode} />
              </span>
            </td>
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

