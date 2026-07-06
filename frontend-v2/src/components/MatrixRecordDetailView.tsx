import { ArrowLeft } from "lucide-react";
import { api } from "../api/client";
import type { MatrixRecordDetail } from "../types";
import { ErrorPage, PageSkeleton } from "./StateViews";
import { useApi } from "../app/useApi";
import { Metric, ReturnStat, fmtPrice } from "./recordsShared";

/** 矩阵记录详情视图 —— 从 RecordsPage.tsx 抽离。 */
export function MatrixRecordDetailView({ recordId, onBack }: { recordId: number; onBack: () => void }) {
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
