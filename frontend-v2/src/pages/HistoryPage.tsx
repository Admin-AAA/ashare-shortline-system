import { History, Search } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { RecommendationHistoryResponse } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { useUrlRoute } from "../app/useUrlRoute";
import { useApi } from "../app/useApi";

/**
 * 复盘页 —— 推荐历史记录（跨 run，按 trade_date 倒序，带 outcome）。
 * 填补北极星"推荐历史记录页"缺口：用户查看每笔推荐的实际表现，支持分页 + 筛选。
 * 点击任一推荐跳转 /run?id=xxx 查看候选详情。
 */
export function HistoryPage() {
  const { navigate } = useUrlRoute();
  const [runMode, setRunMode] = useState<"formal" | "experiment">("formal");
  const [label, setLabel] = useState<"" | "success" | "failed" | "missed" | "pending">("");
  const [codeInput, setCodeInput] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  // code 只在用户提交时触发查询（避免每键都请求）；用本地 state 控制
  const [codeQuery, setCodeQuery] = useState("");

  const { data, loading, error } = useApi<RecommendationHistoryResponse>(
    () =>
      api.recommendationHistory({
        run_mode: runMode,
        code: codeQuery || undefined,
        label: label || undefined,
        limit: pageSize,
        offset: page * pageSize,
      }),
    [runMode, label, codeQuery, page],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const resetPage = () => setPage(0);

  const submitCode = () => {
    setCodeQuery(codeInput.trim());
    resetPage();
  };

  const winCount = items.filter((it) => it.outcome?.outcome_label === "success").length;
  const hasOutcome = items.some((it) => it.outcome);

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">复盘</span>
        <h2 className="panel__title">推荐历史</h2>
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-base)" }}>
          共 {total} 笔 · 当前 {page * pageSize + 1}-{Math.min((page + 1) * pageSize, total)}
        </span>
      </div>

      {/* 筛选栏 */}
      <div
        style={{
          display: "flex",
          gap: "var(--space-3)",
          alignItems: "center",
          flexWrap: "wrap",
          padding: "var(--space-3) 0",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-base)" }}>
          <span style={{ color: "var(--muted)" }}>口径</span>
          <select
            value={runMode}
            onChange={(e) => {
              setRunMode(e.target.value as "formal" | "experiment");
              resetPage();
            }}
            style={selectStyle}
          >
            <option value="formal">formal</option>
            <option value="experiment">experiment</option>
          </select>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-base)" }}>
          <span style={{ color: "var(--muted)" }}>结果</span>
          <select
            value={label}
            onChange={(e) => {
              setLabel(e.target.value as typeof label);
              resetPage();
            }}
            style={selectStyle}
          >
            <option value="">全部</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="missed">未触发</option>
            <option value="pending">待标注</option>
          </select>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-base)" }}>
          <span style={{ color: "var(--muted)" }}>代码</span>
          <input
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitCode()}
            placeholder="6 位代码"
            style={{ ...selectStyle, fontFamily: "var(--font-mono)", width: 110 }}
          />
        </label>
        <button onClick={submitCode} style={btnStyle}>
          <Search size={13} /> 查询
        </button>
        {hasOutcome && (
          <span style={{ marginLeft: "auto", fontSize: "var(--text-sm)", color: "var(--muted)" }}>
            本页胜率 {winCount}/{items.length} = {((winCount / items.length) * 100).toFixed(1)}%
          </span>
        )}
      </div>

      {loading ? (
        <PageSkeleton title="复盘" hint="加载推荐历史" rowCount={10} />
      ) : error ? (
        <ErrorPage title="历史加载失败" message={error} />
      ) : items.length === 0 ? (
        <EmptyState icon={<History size={32} />} title="暂无推荐记录" reason="该筛选条件下没有推荐" />
      ) : (
        <>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>日期</th>
                <th style={thStyle}>股票</th>
                <th style={{ ...thStyle, textAlign: "right" }}>分数</th>
                <th style={thStyle}>买点</th>
                <th style={thStyle}>结果</th>
                <th style={{ ...thStyle, textAlign: "right" }}>收益</th>
                <th style={thStyle}>退出原因</th>
                <th style={thStyle}>持仓</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const o = it.outcome;
                const ret = o?.executed_return;
                const isWin = o?.outcome_label === "success";
                const labelColor =
                  o?.outcome_label === "success"
                    ? "var(--market-up)"
                    : o?.outcome_label === "failed"
                      ? "var(--market-down)"
                      : "var(--muted)";
                return (
                  <tr
                    key={it.recommendation_id}
                    onClick={() => navigate(`/run`, { params: { run_id: it.run_id } })}
                    style={rowStyle}
                  >
                    <td style={tdStyle}>{it.trade_date}</td>
                    <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>
                      {it.code} <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{it.name}</span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                      {it.final_score.toFixed(1)}
                    </td>
                    <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                      {it.setup_type || "—"}
                    </td>
                    <td style={{ ...tdStyle, color: labelColor, fontWeight: 600 }}>
                      {o?.outcome_label ?? "—"}
                    </td>
                    <td
                      style={{
                        ...tdStyle,
                        textAlign: "right",
                        fontFamily: "var(--font-mono)",
                        color: ret == null ? "var(--muted)" : ret >= 0 ? "var(--market-up)" : "var(--market-down)",
                      }}
                    >
                      {ret == null ? "—" : `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%`}
                    </td>
                    <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                      {o?.exit_reason ?? "—"}
                    </td>
                    <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                      {o?.holding_days != null ? `${o.holding_days}d` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* 分页 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-3)",
              padding: "var(--space-3) 0",
              borderTop: "1px solid var(--border)",
            }}
          >
            <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0} style={pageBtnStyle(page === 0)}>
              上一页
            </button>
            <span style={{ fontSize: "var(--text-base)", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              style={pageBtnStyle(page >= totalPages - 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  background: "var(--surface-soft)",
  color: "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "6px 10px",
  fontSize: "var(--text-base)",
};

const btnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-2)",
  background: "var(--accent)",
  color: "var(--surface)",
  border: "none",
  borderRadius: 6,
  padding: "6px 12px",
  fontSize: "var(--text-base)",
  cursor: "pointer",
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
  cursor: "pointer",
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
