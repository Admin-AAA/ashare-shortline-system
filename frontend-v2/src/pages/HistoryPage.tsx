import { ChevronDown, ChevronRight, History, Loader2, Search } from "lucide-react";
import { Fragment, useCallback, useState } from "react";
import { api } from "../api/client";
import type { RecommendationDetail, RecommendationHistoryResponse } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { CollapsiblePanel } from "../components/CollapsiblePanel";
import { useApi } from "../app/useApi";
import { RecommendationDetailCard } from "../components/RecommendationDetailCard";
import { fmtPrice } from "../components/Shared";
import { exitReasonLabel } from "../utils/display";

/**
 * 复盘页 —— 推荐历史记录（跨 run，按 trade_date 倒序，带 outcome）。
 * 填补北极星"推荐历史记录页"缺口：用户查看每笔推荐的实际表现，支持分页 + 筛选。
 * 点击行展开 inline 详情（评分分解 / 买点 / 风控 / LLM 摘要）。
 */
export function HistoryPage() {
  const [label, setLabel] = useState<"" | "success" | "failed" | "missed" | "pending">("");
  const [codeInput, setCodeInput] = useState("");
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, RecommendationDetail>>({});
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<Record<string, string>>({});
  const pageSize = 50;

  // code 只在用户提交时触发查询（避免每键都请求）；用本地 state 控制
  const [codeQuery, setCodeQuery] = useState("");

  const { data, loading, error } = useApi<RecommendationHistoryResponse>(
    () =>
      api.recommendationHistory({
        code: codeQuery || undefined,
        label: label || undefined,
        limit: pageSize,
        offset: page * pageSize,
      }),
    [label, codeQuery, page],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const resetPage = () => setPage(0);

  const submitCode = () => {
    setCodeQuery(codeInput.trim());
    resetPage();
  };

  const handleToggle = useCallback(
    async (id: string) => {
      if (expandedId === id) {
        setExpandedId(null);
        return;
      }
      setExpandedId(id);
      if (details[id]) return;
      setDetailLoadingId(id);
      setDetailError((prev) => ({ ...prev, [id]: "" }));
      try {
        const detail = await api.recommendationDetail(id);
        setDetails((prev) => ({ ...prev, [id]: detail }));
      } catch (e) {
        setDetailError((prev) => ({ ...prev, [id]: (e as Error).message || "加载失败" }));
      } finally {
        setDetailLoadingId(null);
      }
    },
    [expandedId, details],
  );

  // 胜率只统计已触发且已结算的推荐（success/failed），pending/missed/outcome 为空的不计入分母
  const itemsWithResult = items.filter(
    (it) => it.outcome?.outcome_label === "success" || it.outcome?.outcome_label === "failed"
  );
  const winCount = itemsWithResult.filter((it) => it.outcome?.outcome_label === "success").length;
  const hasOutcome = itemsWithResult.length > 0;

  return (
    <CollapsiblePanel
      collapsedKey="history-recs"
      kicker="复盘"
      title="推荐历史"
      actions={
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-base)" }}>
          共 {total} 笔 · 当前 {page * pageSize + 1}-{Math.min((page + 1) * pageSize, total)}
        </span>
      }
    >
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
            本页胜率 {winCount}/{itemsWithResult.length} = {((winCount / itemsWithResult.length) * 100).toFixed(1)}%
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
                <th style={thStyle}>策略/买点</th>
                <th style={thStyle}>价格/风控</th>
                <th style={thStyle}>执行规则</th>
                <th style={thStyle}>结果</th>
                <th style={{ ...thStyle, textAlign: "right" }}>收益</th>
                <th style={thStyle}>退出/持仓</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const o = it.outcome;
                const ret = o?.executed_return;
                const labelColor =
                  o?.outcome_label === "success"
                    ? "var(--market-up)"
                    : o?.outcome_label === "failed"
                      ? "var(--market-down)"
                      : "var(--muted)";
                return (
                  <Fragment key={it.recommendation_id}>
                    <tr
                      onClick={() => handleToggle(it.recommendation_id)}
                      style={rowStyle}
                      title="点击展开完整决策详情"
                    >
                      <td style={tdStyle}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
                          {expandedId === it.recommendation_id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          {it.trade_date}
                        </span>
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "var(--font-mono)" }}>
                        {it.code} <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{it.name}</span>
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                        {it.final_score.toFixed(1)}
                      </td>
                      <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                        <div style={stackStyle}>
                          <span style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>{it.bucket || "—"}</span>
                          <span>{it.setup_type || "未命中买点"}</span>
                        </div>
                      </td>
                      <td style={{ ...tdStyle, fontSize: "var(--text-sm)" }}>
                        <div style={stackStyle}>
                          <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                            买 {fmtPrice(it.recommend_price)} · 止 {fmtPrice(it.stop_loss)} · 盈 {fmtPrice(it.take_profit)}
                          </span>
                          <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                            理想 {fmtPrice(it.ideal_buy)} · 次买 {fmtPrice(it.secondary_buy)}
                          </span>
                        </div>
                      </td>
                      <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)", maxWidth: 320 }}>
                        <div style={stackStyle}>
                          <span title={it.entry_rule || ""}>入场: {clip(it.entry_rule || "—", 32)}</span>
                          <span title={it.invalid_rule || ""}>失效: {clip(it.invalid_rule || "—", 32)}</span>
                        </div>
                      </td>
                      <td style={{ ...tdStyle, color: labelColor, fontWeight: 600 }}>
                        {outcomeLabel(o?.outcome_label)}
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
                        <div style={stackStyle}>
                          <span>{exitReasonLabel(o?.exit_reason, "—", ret)}</span>
                          <span>{o?.holding_days != null ? `持仓 ${o.holding_days} 天` : "—"}</span>
                        </div>
                      </td>
                    </tr>
                    {expandedId === it.recommendation_id && (
                      <tr>
                        <td colSpan={9} style={{ padding: 0, borderBottom: "1px solid var(--border)" }}>
                          {detailLoadingId === it.recommendation_id ? (
                            <div style={detailStateStyle}>
                              <Loader2 size={16} className="spin" /> 加载完整决策详情…
                            </div>
                          ) : detailError[it.recommendation_id] ? (
                            <div style={{ ...detailStateStyle, color: "var(--market-down)" }}>
                              {detailError[it.recommendation_id]}
                            </div>
                          ) : (
                            <RecommendationDetailCard item={details[it.recommendation_id] || it} />
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
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
    </CollapsiblePanel>
  );
}

const detailStateStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-2)",
  padding: "var(--space-4)",
  color: "var(--muted)",
  background: "var(--surface-soft)",
  fontSize: "var(--text-sm)",
};

const stackStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const clip = (value: string, max: number) => {
  if (value.length <= max) return value;
  return `${value.slice(0, max)}…`;
};

const outcomeLabel = (value: string | null | undefined) => {
  if (value === "success") return "成功";
  if (value === "failed") return "失败";
  if (value === "missed") return "未触发";
  if (value === "pending") return "待标注";
  return value || "—";
};

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
