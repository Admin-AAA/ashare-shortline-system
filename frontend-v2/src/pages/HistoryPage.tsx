import { ChevronDown, ChevronRight, History, Layers, Loader2, RotateCcw, Search } from "lucide-react";
import { Fragment, useCallback, useState } from "react";
import { api } from "../api/client";
import type { RecommendationDetail, RecommendationHistoryResponse } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { Panel } from "../components/Panel";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";
import { RecommendationDetailCard } from "../components/RecommendationDetailCard";
import { fmtPrice } from "../components/Shared";
import { bucketLabel, exitReasonLabel, BUCKET_LABELS } from "../utils/display";

/**
 * 复盘页 —— 推荐历史记录（跨 run，按 trade_date 倒序，带 outcome）。
 * 填补北极星"推荐历史记录页"缺口：用户查看每笔推荐的实际表现，支持分页 + 筛选。
 * 点击行展开 inline 详情（评分分解 / 买点 / 风控 / LLM 摘要）。
 *
 * 策略下钻（北极星 C 项）：从策略清单页点击策略卡进入本页，URL 带
 * ?strategy_id=...&strategy_name=...，仅展示该策略的全部推荐历史。
 */
export function HistoryPage() {
  const { navigate } = useUrlRoute();
  const strategyId = queryParam("strategy_id");
  const strategyName = queryParam("strategy_name");
  const [label, setLabel] = useState<"" | "success" | "failed" | "missed" | "pending">("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, RecommendationDetail>>({});
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<Record<string, string>>({});
  const pageSize = 50;

  // code/setupType 只在用户提交时触发查询（避免每键都请求）；用本地 state 控制
  // bucket 用下拉框，选择即查询
  const [codeInput, setCodeInput] = useState("");
  const [codeQuery, setCodeQuery] = useState("");
  const [bucket, setBucket] = useState("");
  const [setupTypeInput, setSetupTypeInput] = useState("");
  const [setupTypeQuery, setSetupTypeQuery] = useState("");

  const { data, loading, error } = useApi<RecommendationHistoryResponse>(
    () =>
      api.recommendationHistory({
        code: codeQuery || undefined,
        label: label || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        bucket: bucket || undefined,
        setup_type: setupTypeQuery || undefined,
        strategy_id: strategyId || undefined,
        limit: pageSize,
        offset: page * pageSize,
      }),
    [label, codeQuery, bucket, setupTypeQuery, dateFrom, dateTo, page, strategyId],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const resetPage = () => setPage(0);

  const submitText = () => {
    setCodeQuery(codeInput.trim());
    setSetupTypeQuery(setupTypeInput.trim());
    resetPage();
  };

  const resetFilters = () => {
    setLabel("");
    setDateFrom("");
    setDateTo("");
    setCodeInput("");
    setCodeQuery("");
    setBucket("");
    setSetupTypeInput("");
    setSetupTypeQuery("");
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

  // 收益率：本页有 executed_return 的推荐均值（盈亏样本）
  const itemsWithReturn = items.filter((it) => it.outcome?.executed_return != null);
  const retAvg =
    itemsWithReturn.length > 0
      ? itemsWithReturn.reduce((acc, it) => acc + (it.outcome?.executed_return ?? 0), 0) / itemsWithReturn.length
      : 0;
  const hasReturn = itemsWithReturn.length > 0;

  return (
    <Panel
      kicker="复盘"
      title="推荐历史"
      actions={
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-base)" }}>
          共 {total} 笔 · 当前 {page * pageSize + 1}-{Math.min((page + 1) * pageSize, total)}
        </span>
      }
    >
      {/* 策略下钻横幅（北极星 C 项）：从策略清单页进入，仅展示该策略推荐历史 */}
      {strategyId && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            flexWrap: "wrap",
            padding: "var(--space-2) var(--space-4)",
            marginBottom: "var(--space-3)",
            background: "var(--surface-soft)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-md)",
          }}
        >
          <Layers size={14} className="text-muted" />
          <span className="text-muted">策略下钻</span>
          <span style={{ fontWeight: 650, color: "var(--ink)" }}>{strategyName || strategyId}</span>
          <span className="text-muted">的推荐历史</span>
          <span className="tag" style={{ marginLeft: "auto", fontSize: "var(--text-xs)" }}>{total} 笔</span>
          <button
            className="btn btn--ghost"
            onClick={() => navigate("/strategies")}
            style={{ padding: "4px 10px", fontSize: "var(--text-sm)" }}
          >
            返回策略清单
          </button>
        </div>
      )}
      {/* 筛选栏（极地海洋玻璃态） */}
      <div className="records-filterbar">
        {/* 日期范围 */}
        <div className="records-filterbar__group">
          <label className="records-filterbar__label">日期</label>
          <input className="cfg-input records-filterbar__date" type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); resetPage(); }} />
          <span className="records-filterbar__arrow">→</span>
          <input className="cfg-input records-filterbar__date" type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); resetPage(); }} />
        </div>

        {/* 结果筛选 */}
        <div className="records-filterbar__group">
          <label className="records-filterbar__label">结果</label>
          <select
            className="cfg-input records-filterbar__select"
            value={label}
            onChange={(e) => {
              setLabel(e.target.value as typeof label);
              resetPage();
            }}
          >
            <option value="">全部</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="missed">未触发</option>
            <option value="pending">待标注</option>
          </select>
        </div>

        {/* 股票代码 */}
        <div className="records-filterbar__group">
          <label className="records-filterbar__label">代码</label>
          <input
            className="cfg-input records-filterbar__select"
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitText()}
            placeholder="6 位代码"
            style={{ width: 110, fontFamily: "var(--font-mono)" }}
          />
        </div>

        {/* 策略桶（下拉选择） */}
        <div className="records-filterbar__group">
          <label className="records-filterbar__label">策略桶</label>
          <select
            className="cfg-input records-filterbar__select"
            value={bucket}
            onChange={(e) => { setBucket(e.target.value); resetPage(); }}
            style={{ minWidth: 150 }}
          >
            <option value="">全部</option>
            {Object.entries(BUCKET_LABELS).map(([key, labelText]) => (
              <option key={key} value={key}>{labelText}</option>
            ))}
          </select>
        </div>

        {/* 买点类型 */}
        <div className="records-filterbar__group">
          <label className="records-filterbar__label">买点</label>
          <input
            className="cfg-input records-filterbar__select"
            value={setupTypeInput}
            onChange={(e) => setSetupTypeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitText()}
            placeholder="setup_type"
            style={{ width: 130, fontFamily: "var(--font-mono)" }}
          />
        </div>

        {/* 操作按钮区 */}
        <button className="btn btn--primary" onClick={submitText} style={{ padding: "6px 14px", fontSize: "var(--text-sm)" }}>
          <Search size={13} /> 查询
        </button>

        <button className="btn btn--ghost records-filterbar__reset" onClick={resetFilters} title="清空所有筛选" style={{ padding: "6px 10px", fontSize: "var(--text-sm)" }}>
          <RotateCcw size={13} /> 重置
        </button>

        {/* 总计数（轻量） */}
        <span className="records-filterbar__total">
          共 {total} 笔
        </span>

        {/* 胜率徽标（醒目卡片） */}
        {hasOutcome && (() => {
          const rate = (winCount / itemsWithResult.length) * 100;
          const tier = rate >= 60 ? "high" : rate >= 40 ? "mid" : "low";
          return (
            <div className={`winrate-badge winrate-badge--${tier}`} title={`本页胜率：${winCount}/${itemsWithResult.length} 已结算推荐`}>
              <span className="winrate-badge__label">胜率</span>
              <span className="winrate-badge__fraction">{winCount}/{itemsWithResult.length}</span>
              <div className="winrate-badge__bar">
                <div className="winrate-bar__fill" style={{ width: `${rate}%` }} />
              </div>
              <span className="winrate-badge__value">{rate.toFixed(1)}%</span>
            </div>
          );
        })()}

        {/* 收益率徽标（配对胜率，A股涨红跌绿） */}
        {hasReturn && (
          <div
            className={`return-badge return-badge--${retAvg >= 0 ? "up" : "down"}`}
            title={`本页收益率：基于 ${itemsWithReturn.length} 笔已结算推荐的平均收益`}
          >
            <span className="return-badge__label">收益率</span>
            <span className="return-badge__value">{retAvg >= 0 ? "+" : ""}{retAvg.toFixed(2)}%</span>
          </div>
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
                // 盈亏口径：executed_return 基于「次日开盘买入」（行业标准，A股 T+1 盘后推荐
                // 只能次日买）。推荐价(recommend_price)是信号日收盘参考价，不是用户真实成本——
                // 跳空低开时两者背离会让用户误以为「按推荐价算亏了」，故此处只展示真实口径，
                // 推荐价→实入场的隔夜跳空作为独立市场风险维度放在价格列，不混进收益数字。
                const recPrice = it.recommend_price;
                const overnightGap = o?.entry_price != null && recPrice != null && recPrice > 0
                  ? (o.entry_price - recPrice) / recPrice * 100
                  : null;
                const isGapEntry = overnightGap != null && Math.abs(overnightGap) > 3;
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
                          <span style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>{bucketLabel(it.bucket)}</span>
                          <span>{it.setup_type || "未命中买点"}</span>
                        </div>
                      </td>
                      <td style={{ ...tdStyle, fontSize: "var(--text-sm)" }}>
                        <div style={stackStyle}>
                          {isGapEntry && o?.entry_price != null ? (
                            // 跳空入场：主显示用户真实交易价（次日开盘买入后的重算执行价）
                            // 推荐设计价降为次要参考——它是信号日收盘价，不是用户真实成本
                            <>
                              <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                                实入场 {fmtPrice(o.entry_price)} · 实止损 {fmtPrice(o.stop_loss_price)} · 实止盈 {fmtPrice(o.take_profit_price)}
                              </span>
                              <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}>
                                信号价 {fmtPrice(recPrice)}（隔夜跳空 {overnightGap! >= 0 ? "+" : ""}{overnightGap!.toFixed(1)}%，市场风险）· 理想 {fmtPrice(it.ideal_buy)} · 次买 {fmtPrice(it.secondary_buy)}
                              </span>
                            </>
                          ) : (
                            <>
                              <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                                买 {fmtPrice(it.recommend_price)} · 止 {fmtPrice(it.stop_loss)} · 盈 {fmtPrice(it.take_profit)}
                              </span>
                              <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                                理想 {fmtPrice(it.ideal_buy)} · 次买 {fmtPrice(it.secondary_buy)}
                              </span>
                            </>
                          )}
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
                        <div style={stackStyle}>
                          <span>{ret == null ? "—" : `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%`}</span>
                          {ret != null && isGapEntry && (
                            <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)", opacity: 0.8 }}>
                              基于实入场
                            </span>
                          )}
                        </div>
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
    </Panel>
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
