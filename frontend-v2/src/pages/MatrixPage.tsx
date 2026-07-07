import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { LayoutGrid, PlayCircle, Eye, Flame, Loader2 } from "lucide-react";
import { api } from "../api/client";
import { EmptyState } from "../components/StateViews";

/**
 * 矩阵页 —— 股票矩阵分析的入口、进度与结果报告
 * 矩阵批量回测通过 POST /api/runs/matrix 触发，status 轮询。
 * 完成后展示 8 个预设的扫描/评分/推荐明细，并在「行情过热·0 推荐」时
 * 明确告知用户这是系统主动防御（非 Bug），同时给出高潮观察池。
 */

interface MatrixWatchItem {
  code?: string;
  name?: string;
  industry?: string;
  final_score?: number;
  effective_score_line?: number;
  watchlist_gap?: number;
  hit_strategies?: string[];
  note?: string;
}

interface MatrixRunRow {
  preset_id: string;
  preset_name: string;
  run_id: string;
  status: string;
  recommended_count: number;
  universe_count?: number;
  scored_count?: number | null;
  effective_score_line?: number;
  market_base?: string;
  market_sentiment?: string;
  top_candidate_score?: number;
  climax_watchlist?: MatrixWatchItem[];
  result_summary?: Record<string, unknown>;
}

interface MatrixResult {
  matrix_batch_id: string;
  matrix_batch_name: string;
  preset_count: number;
  run_count: number;
  completed_count: number;
  failed_count: number;
  recommended_count: number;
  runs: MatrixRunRow[];
  market_regime?: string | null;
  market_base?: string | null;
  market_sentiment?: string | null;
  top_candidate_score?: number | null;
  aggregated_watchlist?: MatrixWatchItem[];
  all_zero?: boolean;
  summary_text?: string;
}

const AMBER_SOFT = "#fff8ed";
const AMBER_BORDER = "#f59e0b";
const AMBER_INK = "#b45309";
const AMBER_MUTED = "#92400e";

export function MatrixPage() {
  const [starting, setStarting] = useState(false);
  const [status, setStatus] = useState("");
  const [taskRun, setTaskRun] = useState<Record<string, unknown> | null>(null);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState("");

  const pull = async () => {
    try {
      const res = await api.matrixRunStatus();
      if (res.task_run) setTaskRun(res.task_run as unknown as Record<string, unknown>);
    } catch {
      /* 空闲拉取失败不阻塞页面 */
    }
  };

  useEffect(() => {
    pull();
  }, []);

  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(async () => {
      try {
        const res = await api.matrixRunStatus();
        const tr = res.task_run;
        if (tr) setTaskRun(tr as unknown as Record<string, unknown>);
        if (tr && (tr.status === "completed" || tr.status === "failed" || tr.status === "error")) {
          setPolling(false);
        }
      } catch {
        /* 轮询容错 */
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [polling]);

  const startMatrix = () => {
    setStarting(true);
    setStatus("");
    setError("");
    api
      .startMatrixRun({})
      .then((res) => {
        setStatus(res.started ? "矩阵回测已启动，正在串行执行全部启用预设…" : "已有任务运行中");
        if (res.started) setPolling(true);
      })
      .catch((err) => {
        setError(err.message || "启动失败");
      })
      .finally(() => setStarting(false));
  };

  const result = (taskRun?.result || {}) as unknown as MatrixResult;
  const runs: MatrixRunRow[] = result.runs || [];
  const isZero = result.all_zero ?? result.recommended_count === 0;
  const isHot = result.market_base === "强牛" || result.market_sentiment === "高潮";
  const showSilence = isZero && isHot && runs.length > 0;
  const watchlist = result.aggregated_watchlist || [];
  const tr = taskRun as unknown as { status?: string; summary?: string | null; summary_text?: string };
  const isRunning = tr?.status === "running";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <section className="panel">
        <div className="panel__header">
          <div>
            <span className="panel__kicker">矩阵</span>
            <h2 className="panel__title">
              <LayoutGrid size={18} />
              股票矩阵
            </h2>
          </div>
          <button className="btn btn--primary" onClick={startMatrix} disabled={starting || isRunning}>
            {starting || isRunning ? <Loader2 size={16} className="spin" /> : <PlayCircle size={16} />}
            {isRunning ? "执行中…" : starting ? "启动中…" : "启动矩阵回测"}
          </button>
        </div>

        {status && (
          <div
            style={{
              marginTop: "var(--space-3)",
              padding: "var(--space-3)",
              background: "var(--surface-soft)",
              borderRadius: "var(--radius-md)",
              fontSize: "var(--text-sm)",
              color: "var(--ink-secondary)",
            }}
          >
            {status}
          </div>
        )}
        {error && (
          <div style={{ marginTop: "var(--space-3)", color: "var(--danger)", fontSize: "var(--text-sm)" }}>
            {error}
          </div>
        )}

        {isRunning && (
          <div style={{ marginTop: "var(--space-3)", fontSize: "var(--text-sm)", color: "var(--ink-secondary)" }}>
            <Loader2 size={14} className="spin" style={{ verticalAlign: "middle", marginRight: 6 }} />
            {String(tr?.summary || "正在执行预设…")}
          </div>
        )}
      </section>

      {runs.length === 0 && !isRunning && (
        <EmptyState
          icon={<LayoutGrid size={32} />}
          title="还没有矩阵结果"
          reason="对多只股票批量跑回测、对比全部启用预设。点上方按钮启动一次矩阵批次。"
        />
      )}

      {runs.length > 0 && (
        <>
          {/* 行情过热 · 系统主动防御（非 Bug）状态卡 */}
          {showSilence && (
            <section
              className="panel"
              style={{ borderLeft: `4px solid ${AMBER_BORDER}`, background: AMBER_SOFT }}
            >
              <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-start" }}>
                <Flame size={22} style={{ color: AMBER_BORDER, flexShrink: 0, marginTop: 2 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    <h3 style={{ margin: 0, color: AMBER_INK, fontSize: "var(--text-lg)" }}>
                      市场过热 · 系统主动防御
                    </h3>
                    <span
                      className="tag"
                      style={{ background: AMBER_BORDER, color: "#fff", border: "none" }}
                    >
                      {result.market_regime || "强牛·高潮"}
                    </span>
                    <span
                      className="tag"
                      style={{ color: AMBER_MUTED, borderColor: AMBER_BORDER, background: "transparent" }}
                    >
                      非 Bug · 设计预期
                    </span>
                  </div>
                  <p style={{ margin: "var(--space-2) 0 0", color: AMBER_MUTED, fontSize: "var(--text-sm)" }}>
                    这不是程序故障。当前行情被判定为「{result.market_regime || "强牛·高潮"}」，系统在回测验证的高危阶段
                    <strong>刻意拒不出手</strong>，避免追高被套。本批次 0 条正式推荐属正常结果。
                  </p>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                      gap: "var(--space-2)",
                      marginTop: "var(--space-3)",
                    }}
                  >
                    <SilenceFact label="行情状态" value={result.market_regime || "强牛·高潮"} hint="强牛 +6 / 高潮 +13，分数线合计 +19" />
                    <SilenceFact
                      label="候选最高分"
                      value={result.top_candidate_score != null ? String(result.top_candidate_score) : "—"}
                      hint="低于全部预设有效分数线"
                    />
                    <SilenceFact label="正式推荐" value="0 只" hint="高潮阶段已禁用动态降线" />
                    <SilenceFact
                      label="回测依据"
                      value="高潮胜率 38~58%"
                      hint="追高风险大，宁可不出手"
                    />
                  </div>

                  <p style={{ margin: "var(--space-3) 0 0", color: AMBER_MUTED, fontSize: "var(--text-xs)" }}>
                    如怀疑是数据滞后导致，可先「同步最新行情」再重跑；下方观察池列出最接近过线、被多策略命中的候选，仅供轻仓观察参考，非正式推荐。
                  </p>
                </div>
              </div>
            </section>
          )}

          {/* 矩阵批次明细表 */}
          <section className="panel">
            <div className="panel__header">
              <div>
                <div className="panel__kicker">矩阵批次报告</div>
                <h3 className="panel__title">{result.matrix_batch_name || "预设矩阵"}</h3>
              </div>
              <span className="tag">
                {result.completed_count ?? runs.length}/{result.preset_count ?? runs.length} 预设完成
                {result.failed_count ? ` · ${result.failed_count} 失败` : ""}
              </span>
            </div>

            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-sm)" }}>
                <thead>
                  <tr style={{ color: "var(--muted)", textAlign: "right" }}>
                    <th style={thStyle}>预设</th>
                    <th style={thStyle}>扫描池</th>
                    <th style={thStyle}>进入评分</th>
                    <th style={{ ...thStyle, color: "var(--ink)" }}>推荐</th>
                    <th style={thStyle}>有效分数线</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.preset_id} style={{ borderTop: "1px solid var(--line)" }}>
                      <td style={{ ...tdStyle, textAlign: "left", fontWeight: 600, color: "var(--ink)" }}>
                        {r.preset_name}
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>{r.universe_count ?? "—"}</td>
                      <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>
                        {r.scored_count != null ? r.scored_count : "—"}
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "var(--font-mono)", color: r.recommended_count === 0 ? "var(--muted)" : "var(--accent)" }}>
                        {r.recommended_count}
                        {r.recommended_count === 0 && (
                          <span style={{ marginLeft: 6, fontSize: "var(--text-xs)", color: "var(--muted)" }}>
                            未过线
                          </span>
                        )}
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>
                        {r.effective_score_line != null ? r.effective_score_line.toFixed(1) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* 高潮观察池 */}
          <section className="panel">
            <div className="panel__header">
              <div>
                <div className="panel__kicker">
                  <Eye size={13} style={{ verticalAlign: "middle" }} /> 高潮观察池
                </div>
                <h3 className="panel__title">轻仓观察 · 非正式推荐</h3>
              </div>
              <span className="tag">{watchlist.length} 只候选</span>
            </div>

            {watchlist.length === 0 ? (
              <div className="text-muted" style={{ fontSize: "var(--text-sm)", padding: "var(--space-2) 0" }}>
                本次无接近过线（或未被多策略命中）的候选，观察池为空。这与「行情过热、系统全面防御」一致。
              </div>
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                  gap: "var(--space-3)",
                  marginTop: "var(--space-2)",
                }}
              >
                {watchlist.map((w) => (
                  <WatchCard key={w.code} item={w} />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function SilenceFact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div
      style={{
        background: "rgba(255,255,255,0.6)",
        border: `1px solid ${AMBER_BORDER}`,
        borderRadius: "var(--radius-md)",
        padding: "var(--space-2) var(--space-3)",
      }}
    >
      <div style={{ fontSize: "var(--text-xs)", color: AMBER_MUTED }}>{label}</div>
      <div style={{ fontSize: "var(--text-base)", fontWeight: 700, color: AMBER_INK, fontFamily: "var(--font-mono)" }}>
        {value}
      </div>
      {hint && <div style={{ fontSize: "var(--text-xs)", color: AMBER_MUTED, marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

function WatchCard({ item }: { item: MatrixWatchItem }) {
  const gap = item.watchlist_gap != null ? Math.round(item.watchlist_gap) : null;
  return (
    <div
      style={{
        border: "1px solid var(--line-strong)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-3)",
        background: "var(--surface)",
        transition: "transform 150ms ease, box-shadow 150ms ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-2px)";
        e.currentTarget.style.boxShadow = "var(--shadow-md)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "none";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="text-mono" style={{ fontWeight: 700, color: "var(--ink)" }}>{item.code}</span>
        <span className="text-mono" style={{ fontSize: "var(--text-lg)", color: "var(--accent)" }}>
          {item.final_score != null ? item.final_score.toFixed(2) : "—"}
        </span>
      </div>
      <div style={{ fontSize: "var(--text-sm)", color: "var(--ink)", marginTop: 2 }}>{item.name}</div>
      <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", marginTop: 4, flexWrap: "wrap" }}>
        {item.industry && <span className="tag">{item.industry}</span>}
        {gap != null && (
          <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>距线 −{gap} 分</span>
        )}
      </div>
      {item.hit_strategies && item.hit_strategies.length > 0 && (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: "var(--space-2)" }}>
          {item.hit_strategies.map((s) => (
            <span
              key={s}
              style={{
                fontSize: "var(--text-xs)",
                padding: "1px 6px",
                borderRadius: 999,
                background: "var(--surface-soft)",
                color: "var(--ink-secondary)",
                border: "1px solid var(--line)",
              }}
            >
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

const thStyle: CSSProperties = {
  padding: "var(--space-2) var(--space-3)",
  fontWeight: 600,
  fontSize: "var(--text-xs)",
  whiteSpace: "nowrap",
};

const tdStyle: CSSProperties = {
  padding: "var(--space-2) var(--space-3)",
  color: "var(--ink-secondary)",
};
