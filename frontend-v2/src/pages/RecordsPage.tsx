import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ChevronRight,
  LayoutGrid,
  ListChecks,
  PlayCircle,
  RotateCcw,
} from "lucide-react";
import { api } from "../api/client";
import { ResumeRunButton } from "../ui/ResumeRunButton";
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
import { ScoreLineCell, ScoreLineMetricValue } from "../components/ScoreLineCell";
import { RunFunnelSection } from "../components/RunFunnelSection";
import { RunStockScoreSection } from "../components/RunStockScoreSection";
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
import { bucketLabel, exitReasonLabel } from "../utils/display";

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
        <div>
          <div className="panel__kicker">记录中心</div>
          <h2 className="panel__title">运行记录复盘</h2>
        </div>
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

type RunSortKey = "trade_date" | "recommended_count" | "score_line" | "return_1d" | "return_3d" | "return_5d" | "finished_at";

const FULL_REPLAY_RUN_NAME = "REPLAY-全量重放";

function RunRecordsList() {
  const { navigate } = useUrlRoute();
  const { data, loading, error } = useApi<RunSummary[]>(
    (_signal) => api.runRecords({ limit: 500 }),
    [],
  );

  // —— 筛选条件（默认近30天内已完成的 formal 运行，推荐数不限）——
  // 推荐数默认不限：弱势市下自动正式推荐经常 0 出票，按推荐数>0 过滤会让这些真实执行过的运行从列表消失。
  // datePreset：近7天/近30天/本月/全部/自定义；自定义时用 dateFrom/dateTo 手选
  const [datePreset, setDatePreset] = useState<"7d" | "30d" | "month" | "all" | "custom">("30d");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("completed");
  const [presetFilter, setPresetFilter] = useState<string>("__all__");
  const [modeFilter, setModeFilter] = useState<string>("formal");
  // 推荐数下限：空 = 不筛选（任意推荐数）；填数字 = recommended_count > N。
  // 手动填 N=0 时，保留全量重放的零推荐记录，便于复盘完整筛选过程。
  const [minRecsFilter, setMinRecsFilter] = useState<string>("");

  // —— 排序 ——
  const [sortKey, setSortKey] = useState<RunSortKey>("trade_date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // —— 分页 ——
  const [page, setPage] = useState(0);
  const pageSize = 20;

  const resetPage = () => setPage(0);

  // 日期预设 → 实际 dateFrom/dateTo（基于今天，trade_date 是 YYYY-MM-DD）。
  // custom 时用用户手选的 dateFrom/dateTo。
  const effectiveDateRange = useMemo(() => {
    if (datePreset === "custom") return { from: dateFrom, to: dateTo };
    if (datePreset === "all") return { from: "", to: "" };
    const today = new Date();
    const to = today.toISOString().slice(0, 10);
    const from = new Date(today);
    if (datePreset === "7d") from.setDate(from.getDate() - 6);
    else if (datePreset === "30d") from.setDate(from.getDate() - 29);
    else if (datePreset === "month") {
      // 本月：从月初到今天
      from.setDate(1);
    }
    return { from: from.toISOString().slice(0, 10), to };
  }, [datePreset, dateFrom, dateTo]);

  // 策略选项：从数据里动态去重（按 preset_name），按频次倒序
  const presetOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of data ?? []) {
      const name = r.preset_name || "(未命名)";
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
  }, [data]);

  if (loading) return <PageSkeleton title="运行推荐记录" hint="加载历史运行" rowCount={8} />;
  if (error) return <ErrorPage title="记录加载失败" message={error} onRetry={() => window.location.reload()} />;
  if (!data || data.length === 0) {
    return <EmptyState icon={<PlayCircle size={32} />} title="暂无运行记录" reason="还没有跑过推荐管线" />;
  }

  const filtered = data.filter((r) => {
    if (effectiveDateRange.from && r.trade_date < effectiveDateRange.from) return false;
    if (effectiveDateRange.to && r.trade_date > effectiveDateRange.to) return false;
    if (statusFilter !== "__all__" && r.status !== statusFilter) return false;
    if (presetFilter !== "__all__" && (r.preset_name || "(未命名)") !== presetFilter) return false;
    if (modeFilter !== "__all__" && r.run_mode !== modeFilter) return false;
    // 推荐数：空 = 不筛选；否则 recommended_count > N。默认 N=0 时全量重放例外保留。
    if (minRecsFilter.trim() !== "") {
      const min = Number(minRecsFilter);
      const keepsFullReplay = min === 0 && r.run_name === FULL_REPLAY_RUN_NAME;
      if (!Number.isNaN(min) && !(r.recommended_count > min) && !keepsFullReplay) return false;
    }
    return true;
  });

  // 排序
  const getSortVal = (r: RunSummary): number | string => {
    if (sortKey === "trade_date") return r.trade_date;
    if (sortKey === "recommended_count") return r.recommended_count;
    if (sortKey === "score_line") return r.score_line;
    if (sortKey === "return_1d") return r.returns?.return_1d ?? -Infinity;
    if (sortKey === "return_3d") return r.returns?.return_3d ?? -Infinity;
    if (sortKey === "return_5d") return r.returns?.return_5d ?? -Infinity;
    if (sortKey === "finished_at") return r.finished_at ?? "";
    return "";
  };
  const sorted = [...filtered].sort((a, b) => {
    const va = getSortVal(a);
    const vb = getSortVal(b);
    const cmp = va < vb ? -1 : va > vb ? 1 : 0;
    return sortDir === "asc" ? cmp : -cmp;
  });

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pageItems = sorted.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const handleSort = (key: RunSortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
    resetPage();
  };

  const resetFilters = () => {
    setDatePreset("30d");
    setDateFrom("");
    setDateTo("");
    setStatusFilter("completed");
    setPresetFilter("__all__");
    setModeFilter("formal");
    setMinRecsFilter("");
    resetPage();
  };

  const SortTh = ({
    col,
    align = "left",
    children,
  }: {
    col: RunSortKey;
    align?: "left" | "right";
    children: React.ReactNode;
  }) => {
    const active = sortKey === col;
    const arrow = active ? (sortDir === "asc" ? " ↑" : " ↓") : "";
    return (
      <th
        style={{ ...thStyle, textAlign: align, cursor: "pointer", userSelect: "none", color: active ? "var(--text)" : "var(--muted)" }}
        onClick={() => handleSort(col)}
        title={`按${String(children)}排序`}
      >
        {children}{arrow}
      </th>
    );
  };

  return (
    <>
      <div className="records-filterbar">
        <div className="records-filterbar__group">
          <label className="records-filterbar__label">日期</label>
          <div className="records-filterbar__preset">
            {([
              { key: "7d", label: "近7天" },
              { key: "30d", label: "近30天" },
              { key: "month", label: "本月" },
              { key: "all", label: "全部" },
              { key: "custom", label: "自定义" },
            ] as const).map((opt) => (
              <button
                key={opt.key}
                className={`records-filterbar__chip ${datePreset === opt.key ? "records-filterbar__chip--active" : ""}`}
                onClick={() => { setDatePreset(opt.key); resetPage(); }}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {datePreset === "custom" && (
            <>
              <input className="cfg-input records-filterbar__date" type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); resetPage(); }} />
              <span style={{ color: "var(--muted)" }}>→</span>
              <input className="cfg-input records-filterbar__date" type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); resetPage(); }} />
            </>
          )}
        </div>

        <div className="records-filterbar__group">
          <label className="records-filterbar__label">模式</label>
          <select className="cfg-input records-filterbar__select" value={modeFilter} onChange={(e) => { setModeFilter(e.target.value); resetPage(); }}>
            <option value="__all__">全部</option>
            <option value="formal">实盘</option>
            <option value="experiment">实验</option>
            <option value="backtest">回测</option>
          </select>
        </div>

        <div className="records-filterbar__group">
          <label className="records-filterbar__label">状态</label>
          <select className="cfg-input records-filterbar__select" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); resetPage(); }}>
            <option value="__all__">全部</option>
            <option value="completed">completed</option>
            <option value="failed">failed</option>
            <option value="interrupted">interrupted</option>
          </select>
        </div>

        <div className="records-filterbar__group">
          <label className="records-filterbar__label">策略</label>
          <select className="cfg-input records-filterbar__select" value={presetFilter} onChange={(e) => { setPresetFilter(e.target.value); resetPage(); }}>
            <option value="__all__">全部</option>
            {presetOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>

        <div className="records-filterbar__group">
          <label className="records-filterbar__label">推荐数 &gt;</label>
          <input
            className="cfg-input records-filterbar__select"
            type="number"
            min={0}
            value={minRecsFilter}
            onChange={(e) => { setMinRecsFilter(e.target.value); resetPage(); }}
            placeholder="不限"
            style={{ width: 80 }}
          />
        </div>

        <button className="btn btn--ghost records-filterbar__reset" onClick={resetFilters} title="重置为默认筛选">
          <RotateCcw size={13} /> 重置
        </button>

        <span className="records-filterbar__count">
          {filtered.length} / {data.length} 条
        </span>
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={<PlayCircle size={32} />} title="当前筛选无记录" reason="调整筛选条件后再试" />
      ) : (
        <>
          <table style={tableStyle}>
            <thead>
              <tr>
                <SortTh col="trade_date">日期</SortTh>
                <th style={thStyle}>运行名</th>
                <SortTh col="recommended_count" align="right">推荐数</SortTh>
                <SortTh col="score_line" align="right">分数线</SortTh>
                <SortTh col="return_1d" align="right">一日收益</SortTh>
                <SortTh col="return_3d" align="right">三日收益</SortTh>
                <SortTh col="return_5d" align="right">五日收益</SortTh>
                <th style={thStyle}>状态</th>
                <SortTh col="finished_at">完成时间</SortTh>
                <th style={thStyle}></th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((run) => (
                <tr
                  key={run.run_id}
                  onClick={() => navigate("/records", { params: { tab: "run", run_id: run.run_id } })}
                  style={{ ...rowStyle, cursor: "pointer" }}
                  title="点击查看当次推荐的股票"
                >
                  <td style={tdStyle} className="text-mono">
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
                      {run.trade_date}
                      <RunModeBadge runMode={run.run_mode} evaluationScope={run.evaluation_scope} />
                    </span>
                  </td>
                  <td style={tdStyle}>{run.run_name || run.run_id.slice(0, 12)}</td>
                  <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>{run.recommended_count}</td>
                  <td style={{ ...tdStyle, textAlign: "right" }}>
                    <ScoreLineCell scoreLine={run.score_line} runMode={run.run_mode} />
                  </td>
                  <ReturnCell value={run.returns?.return_1d ?? null} />
                  <ReturnCell value={run.returns?.return_3d ?? null} />
                  <ReturnCell value={run.returns?.return_5d ?? null} />
                  <td style={tdStyle}>
                    <StatusBadge status={run.status} />
                  </td>
                  <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                    {run.finished_at ? formatTime(run.finished_at) : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", whiteSpace: "nowrap" }}>
                    {["failed", "interrupted", "error", "cancelled"].includes(String(run.status).toLowerCase()) && <ResumeRunButton runId={run.run_id} />}
                    <ChevronRight size={14} style={{ color: "var(--muted)", verticalAlign: "middle" }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-3) 0", borderTop: "1px solid var(--border)" }}>
            <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={safePage === 0} style={pageBtnStyle(safePage === 0)}>
              上一页
            </button>
            <span style={{ fontSize: "var(--text-base)", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
              {safePage + 1} / {totalPages} · 共 {filtered.length} 条
            </span>
            <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={safePage >= totalPages - 1} style={pageBtnStyle(safePage >= totalPages - 1)}>
              下一页
            </button>
          </div>
        </>
      )}
    </>
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
    () => api.matrixRecords({ page, page_size: pageSize, exclude_analysis_type: "single_stock" }),
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
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>分数线</span>
            <ScoreLineMetricValue scoreLine={run.score_line} runMode={run.run_mode} />
          </div>
          <Metric label="预设" value={run.preset_name || "—"} />
          <Metric label="完成时间" value={run.finished_at ? formatTime(run.finished_at) : "—"} />
          <Metric label="收益基准日" value={as_of_date || "—"} mono />
        </div>
      </section>

      <RunFunnelSection runId={runId} />

      <RunStockScoreSection runId={runId} />

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
                      <span style={{ fontFamily: "var(--font-mono)" }}>{bucketLabel(rec.bucket)}</span>
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

