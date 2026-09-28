import { ChevronDown, ChevronRight, History, Layers, Loader2, Maximize2, RotateCcw, Search, Zap } from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  ExecutionConfirmationItem,
  ExecutionConfirmationResponse,
  ExecutionConditionSummary,
  RecommendationDetail,
  RecommendationExecutionState,
  RecommendationHistoryResponse,
  RealtimePnlItem,
} from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { Panel } from "../components/Panel";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";
import { RecommendationDetailCard } from "../components/RecommendationDetailCard";
import { fmtPrice } from "../components/Shared";
import { bucketLabel, exitReasonLabel, formatApiDateTime, isOutcomeSettled, outcomeLabel, outcomeTone, setupLabel, BUCKET_LABELS } from "../utils/display";
import { ruleKeyLevelLabels, rulesMentionKeyLevel } from "../utils/ruleKeyLevels";
import { cancelObservationText, collectUnsettledIds, executionDecisionCode, historyExecutedReturn, holdingDecisionPresentation, isPlaceholderEntryRule } from "../utils/executionConfirmation";

/**
 * 复盘页 —— 正式运行推荐历史（跨 run，按 trade_date 倒序，带 outcome）。
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
  const strategyVersionId = queryParam("strategy_version_id");
  const strategyVersion = queryParam("strategy_version");
  const [label, setLabel] = useState<"" | "success" | "failed" | "missed" | "pending" | "provisional">("");
  // datePreset：近7天/近30天/近60天/本月/全部/自定义；custom 时用 dateFrom/dateTo 手选。
  // 默认近60天，覆盖历史重建的目标观察窗口。
  const [datePreset, setDatePreset] = useState<"7d" | "30d" | "60d" | "month" | "all" | "custom">("60d");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, RecommendationDetail>>({});
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<Record<string, string>>({});
  // 实时盈亏：pending / provisional 推荐的实盘浮盈，纯展示不改结算。
  const [pnlMap, setPnlMap] = useState<Record<string, RealtimePnlItem>>({});
  // 批量次日买入确认：未结算推荐的当前可执行状态，纯展示不改结算。
  const [confirmationMap, setConfirmationMap] = useState<Record<string, ExecutionConfirmationItem>>({});
  const [confirmationErrors, setConfirmationErrors] = useState<Record<string, string>>({});
  const [confirmationLoadingIds, setConfirmationLoadingIds] = useState<Set<string>>(new Set());
  const [confirmationComputedAt, setConfirmationComputedAt] = useState<string | null>(null);
  // 请求序号：分页/筛选变化时旧请求晚到不覆盖新页面（Spec 6.1.5）
  const confirmationReqRef = useRef(0);
  const pageSize = 50;

  // 日期预设 → 实际 dateFrom/dateTo（基于今天）。custom 用手选，all 不限。
  const effectiveDateRange = useMemo(() => {
    if (datePreset === "custom") return { from: dateFrom, to: dateTo };
    if (datePreset === "all") return { from: "", to: "" };
    const localDateKey = (instant: Date) => {
      const year = instant.getFullYear();
      const month = String(instant.getMonth() + 1).padStart(2, "0");
      const day = String(instant.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };
    const today = new Date();
    const to = localDateKey(today);
    const from = new Date(today);
    if (datePreset === "7d") from.setDate(from.getDate() - 6);
    else if (datePreset === "30d") from.setDate(from.getDate() - 29);
    else if (datePreset === "60d") from.setDate(from.getDate() - 59);
    else if (datePreset === "month") from.setDate(1);
    return { from: localDateKey(from), to };
  }, [datePreset, dateFrom, dateTo]);

  // 股票代码保留输入后提交；策略桶和买点均选择即查询。
  const [codeInput, setCodeInput] = useState("");
  const [codeQuery, setCodeQuery] = useState("");
  const [bucket, setBucket] = useState("");
  const [setupType, setSetupType] = useState("");

  const { data, loading, error } = useApi<RecommendationHistoryResponse>(
    () =>
      api.recommendationHistory({
        code: codeQuery || undefined,
        label: label || undefined,
        date_from: effectiveDateRange.from || undefined,
        date_to: effectiveDateRange.to || undefined,
        bucket: bucket || undefined,
        setup_type: setupType || undefined,
        strategy_id: strategyId || undefined,
        strategy_version_id: strategyVersionId || undefined,
        limit: pageSize,
        offset: page * pageSize,
      }),
    [label, codeQuery, bucket, setupType, effectiveDateRange, page, strategyId, strategyVersionId],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const summary = data?.summary;
  const cancelSummary = summary?.cancel_observation;
  const setupTypeOptions = useMemo(
    () =>
      (data?.facets?.setup_types ?? [])
        .map((value) => ({ value, label: setupLabel(value).label }))
        .sort((left, right) => left.label.localeCompare(right.label, "zh-CN")),
    [data?.facets?.setup_types],
  );
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const resetPage = () => setPage(0);

  const submitText = () => {
    setCodeQuery(codeInput.trim());
    resetPage();
  };

  const resetFilters = () => {
    setLabel("");
    setDatePreset("60d");
    setDateFrom("");
    setDateTo("");
    setCodeInput("");
    setCodeQuery("");
    setBucket("");
    setSetupType("");
    resetPage();
  };

  // 当前页未结算推荐 ID（去重 + 排序成稳定 key，避免重渲染重复请求 Spec 9.2）
  const unsettledIds = useMemo(() => collectUnsettledIds(items), [items]);
  const unsettledIdsKey = unsettledIds.join(",");

  // 批量买入确认：自动加载 + 手动刷新共用。只有存在未结算行时才请求（Spec 9.2）。
  const loadExecutionConfirmations = useCallback(
    async (ids: string[], refresh = false) => {
      const reqSeq = ++confirmationReqRef.current;
      if (ids.length === 0) {
        setConfirmationLoadingIds(new Set());
        return;
      }
      setConfirmationLoadingIds(new Set(ids));
      setConfirmationErrors((previous) => {
        const next = { ...previous };
        for (const id of ids) delete next[id];
        return next;
      });
      try {
        const res: ExecutionConfirmationResponse = await api.confirmExecutionBatch(ids, refresh);
        // 旧请求晚到必须丢弃，不能把上一页/旧筛选结果写入当前列表（Spec 6.1.5）
        if (reqSeq !== confirmationReqRef.current) return;
        const itemMap: Record<string, ExecutionConfirmationItem> = {};
        for (const it of res.items) itemMap[it.recommendation_id] = it;
        const errMap: Record<string, string> = {};
        for (const e of res.errors) errMap[e.recommendation_id] = e.message;
        setConfirmationMap((previous) => {
          const next = { ...previous };
          for (const id of ids) delete next[id];
          return Object.assign(next, itemMap);
        });
        setConfirmationErrors((previous) => {
          const next = { ...previous };
          for (const id of ids) delete next[id];
          return Object.assign(next, errMap);
        });
        setPnlMap((previous) => {
          const next = { ...previous };
          for (const id of ids) delete next[id];
          return Object.assign(
            next,
            Object.fromEntries(res.pnl_items.map((item) => [item.recommendation_id, item])),
          );
        });
        setConfirmationComputedAt(res.computed_at);
      } catch (error) {
        if (reqSeq !== confirmationReqRef.current) return;
        const message = (error as Error).message || "实时判断失败";
        setConfirmationErrors((previous) => ({
          ...previous,
          ...Object.fromEntries(ids.map((id) => [id, message])),
        }));
      } finally {
        if (reqSeq === confirmationReqRef.current) {
          setConfirmationLoadingIds(new Set());
        }
      }
    },
    [],
  );

  // 历史数据加载成功且当前页存在未结算行时自动请求（Spec 6.1）
  useEffect(() => {
    loadExecutionConfirmations(unsettledIds);
    // 依赖 unsettledIdsKey（稳定字符串）而非数组引用，避免重渲染重复请求
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unsettledIdsKey, loadExecutionConfirmations]);

  // 统一刷新实时状态：同时刷新未结算实时盈亏与买入确认（Spec 6.3）
  const refreshRealtimeState = useCallback(async () => {
    await loadExecutionConfirmations(unsettledIds, true);
  }, [loadExecutionConfirmations, unsettledIds]);

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

  return (
    <Panel
      kicker="复盘"
      title="推荐历史"
      actions={
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-base)" }}>
          共 {total} 笔 · 当前 {total === 0 ? 0 : page * pageSize + 1}-{Math.min((page + 1) * pageSize, total)}
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
          <span className="text-muted">
            {strategyVersionId ? "直接参与评分的正式推荐" : "的推荐历史"}
          </span>
          {strategyVersionId && (
            <span className="tag" title={strategyVersionId} style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}>
              v{strategyVersion || "历史"} · {shortVersionId(strategyVersionId)}
            </span>
          )}
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
          <div className="records-filterbar__preset">
            {([
              { key: "7d", label: "近7天" },
              { key: "30d", label: "近30天" },
              { key: "60d", label: "近60天" },
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
              <span className="records-filterbar__arrow">→</span>
              <input className="cfg-input records-filterbar__date" type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); resetPage(); }} />
            </>
          )}
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
            <option value="provisional">结算中</option>
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

        {/* 买点类型（数据库完整枚举，中文展示、原始值筛选） */}
        <div className="records-filterbar__group">
          <label className="records-filterbar__label">买点</label>
          <select
            className="cfg-input records-filterbar__select"
            value={setupType}
            onChange={(e) => {
              setSetupType(e.target.value);
              resetPage();
            }}
            style={{ minWidth: 168 }}
          >
            <option value="">全部</option>
            {setupTypeOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        {/* 操作按钮区 */}
        <button className="btn btn--primary" onClick={submitText} style={{ padding: "6px 14px", fontSize: "var(--text-sm)" }}>
          <Search size={13} /> 查询
        </button>

        <button className="btn btn--ghost records-filterbar__reset" onClick={resetFilters} title="清空所有筛选" style={{ padding: "6px 10px", fontSize: "var(--text-sm)" }}>
          <RotateCcw size={13} /> 重置
        </button>

        {/* 刷新实时状态：同时刷新未结算实时盈亏与买入确认（Spec 6.3 统一刷新入口） */}
        <button
          className="btn btn--ghost"
          onClick={() => refreshRealtimeState()}
          disabled={confirmationLoadingIds.size > 0}
          title={
            confirmationComputedAt
              ? `未结算推荐实时盈亏与买入确认 · 上次 ${confirmationComputedAt}`
              : "未结算推荐实时盈亏与买入确认"
          }
          style={{ padding: "6px 12px", fontSize: "var(--text-sm)" }}
        >
          {confirmationLoadingIds.size > 0 ? <Loader2 size={13} className="spin" /> : <Zap size={13} />} 刷新实时状态
        </button>

        {/* 总计数（轻量） */}
        <span className="records-filterbar__total" title="包含 provenance 完整的 LIVE 前瞻与历史重建记录；汇总不等同 LIVE KPI">
          当前筛选 · 共 {total} 笔
        </span>
        <span className="tag tag--warning" title="历史回放会逐条标识；推荐质量 LIVE KPI 仅在指标页 LIVE 前瞻口径统计">
          历史记录可含回放 · 非 LIVE KPI
        </span>

        {/* 胜率徽标（醒目卡片） */}
        {summary?.win_rate != null && (() => {
          const rate = summary.win_rate;
          const tier = rate >= 60 ? "high" : rate >= 40 ? "mid" : "low";
          return (
            <div className={`winrate-badge winrate-badge--${tier}`} title={`当前筛选范围净胜率：${summary.win_count}/${summary.trade_count} 笔已成交正式推荐`}>
              <span className="winrate-badge__label">胜率</span>
              <span className="winrate-badge__fraction">{summary.win_count}/{summary.trade_count}</span>
              <div className="winrate-badge__bar">
                <div className="winrate-bar__fill" style={{ width: `${rate}%` }} />
              </div>
              <span className="winrate-badge__value">{rate.toFixed(1)}%</span>
            </div>
          );
        })()}

        {/* 全筛选范围净收益指标（executed_return 已含滑点，另扣佣金与印花税） */}
        {summary?.average_net_return != null && (
          <div
            className={`return-badge return-badge--${summary.average_net_return >= 0 ? "up" : "down"}`}
            title={`当前筛选范围 ${summary.trade_count} 笔已成交正式推荐的平均单笔净收益，已扣 ${summary.additional_cost_pct.toFixed(2)}% 交易成本`}
          >
            <span className="return-badge__label">平均单笔净收益</span>
            <span className="return-badge__value">{summary.average_net_return >= 0 ? "+" : ""}{summary.average_net_return.toFixed(2)}%</span>
          </div>
        )}

        {summary && summary.trade_count > 0 && (
          <div
            className={`return-badge return-badge--${summary.total_net_return >= 0 ? "up" : "down"}`}
            title="当前筛选范围所有已成交正式推荐的单笔净收益之和；不是账户复利收益率"
          >
            <span className="return-badge__label">累计单笔净收益</span>
            <span className="return-badge__value">{summary.total_net_return >= 0 ? "+" : ""}{summary.total_net_return.toFixed(2)}%</span>
          </div>
        )}

        {cancelSummary && cancelSummary.cancelled_count > 0 && (
          <div
            className="return-badge"
            title="买入条件从未命中、随后被系统取消的推荐；这些票不进入交易成功率、失败率或实际交易收益"
          >
            <span className="return-badge__label">买入前取消</span>
            <span className="return-badge__value">{cancelSummary.cancelled_count} 笔</span>
          </div>
        )}

        {cancelSummary?.average_5d_return != null && (
          <div
            className={`return-badge return-badge--${cancelSummary.average_5d_return >= 0 ? "up" : "down"}`}
            title={`假设忽略取消并按首次取消价买入，观察后第 5 个交易日收盘的平均净收益；共 ${cancelSummary.observed_5d_count} 笔，非实际收益`}
          >
            <span className="return-badge__label">取消后 5 日观察</span>
            <span className="return-badge__value">
              {cancelSummary.average_5d_return >= 0 ? "+" : ""}{cancelSummary.average_5d_return.toFixed(2)}%
            </span>
          </div>
        )}

        {cancelSummary?.avoided_loss_rate_5d != null && (
          <div
            className="return-badge"
            title="取消后第 5 个交易日假设净收益小于或等于 0 的比例"
          >
            <span className="return-badge__label">5 日避免亏损率</span>
            <span className="return-badge__value">{cancelSummary.avoided_loss_rate_5d.toFixed(1)}%</span>
          </div>
        )}

        {/* 未触发买点的旁路参考：这些票没有真实成交，不进胜率/净收益口径，
            但"没买到是躲过亏损还是错过盈利"必须能看到。 */}
        {summary?.missed_observation?.average_reference_return != null && (
          <div
            className={`return-badge return-badge--${summary.missed_observation.average_reference_return >= 0 ? "up" : "down"}`}
            title={`未触发买点、未建仓的 ${summary.missed_observation.observed_count} 笔推荐（另有 ${summary.missed_observation.pending_count} 笔观察窗口未满未计入），假设按推荐价买入并持有到观察期末收盘的平均静态参考收益（已扣 ${summary.additional_cost_pct.toFixed(2)}% 成本）。非实际盈亏。`}
          >
            <span className="return-badge__label">未触发·参考收益</span>
            <span className="return-badge__value">
              {summary.missed_observation.average_reference_return >= 0 ? "+" : ""}
              {summary.missed_observation.average_reference_return.toFixed(2)}%
            </span>
          </div>
        )}

        {summary?.missed_observation?.avoided_loss_rate != null && (
          <div
            className="return-badge"
            title={`未触发推荐里"如果买了会亏"的比例（${summary.missed_observation.avoided_loss_count}/${summary.missed_observation.observed_count} 笔）；越低说明未触发越可能是在错过盈利。`}
          >
            <span className="return-badge__label">未触发避免亏损率</span>
            <span className="return-badge__value">{summary.missed_observation.avoided_loss_rate.toFixed(1)}%</span>
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
                <th style={thStyle}>分数</th>
                <th style={thStyle}>策略/买点</th>
                <th style={thStyle}>价格/风控</th>
                <th style={thStyle}>执行规则</th>
                <th style={thStyle}>结果</th>
                <th style={{ ...thStyle, textAlign: "right" }}>收益</th>
                <th style={{ ...thStyle, textAlign: "right", minWidth: 128 }}>退出/持仓</th>
                <th style={{ ...thStyle, width: 44 }} aria-hidden />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const o = it.outcome;
                const settled = isOutcomeSettled(o?.outcome_label);
                const savedExecution = it.execution_state;
                const cancelObservation = it.cancel_observation;
                const currentCondition = settled
                  ? savedExecution?.condition_summary
                  : confirmationMap[it.recommendation_id]?.condition_summary || savedExecution?.condition_summary;
                const holdingDecision = settled
                  ? null
                  : holdingDecisionPresentation(confirmationMap[it.recommendation_id]);
                const missingEntryRule = isPlaceholderEntryRule(it.entry_rule);
                // 只有真实成交且已结算的记录才有收益；missed 永远显示未成交。
                // provisional 尚不可实现，继续使用实时浮盈。
                const ret = historyExecutedReturn(o);
                // 盈亏口径：executed_return 基于策略声明的实际入场方式。推荐价
                // (recommend_price)是信号日参考价，不是用户真实成本——
                // 跳空低开时两者背离会让用户误以为「按推荐价算亏了」，故此处只展示真实口径，
                // 推荐价→实入场的隔夜跳空作为独立市场风险维度放在价格列，不混进收益数字。
                const recPrice = it.recommend_price;
                const overnightGap = o?.entry_price != null && recPrice != null && recPrice > 0
                  ? (o.entry_price - recPrice) / recPrice * 100
                  : null;
                const isGapEntry = overnightGap != null && Math.abs(overnightGap) > 3;
                const labelTone = outcomeTone(o?.outcome_label);
                const labelColor =
                  labelTone === "positive"
                    ? "var(--market-up)"
                    : labelTone === "danger"
                      ? "var(--market-down)"
                      : labelTone === "warning"
                        ? "var(--market-gold, #b8860b)"
                        : "var(--muted)";
                const matchedVersion = it.strategy_versions?.find((version) => version.strategy_id === strategyId);
                const keyLevelLabels = ruleKeyLevelLabels(it.entry_rule, it.invalid_rule, it.key_levels);
                const hasUnresolvedKeyLevel = keyLevelLabels.length === 0
                  && rulesMentionKeyLevel(it.entry_rule, it.invalid_rule);
                return (
                  <Fragment key={it.recommendation_id}>
                    <tr
                      className={`history-row--expandable${expandedId === it.recommendation_id ? " history-row--expanded" : ""}`}
                      onClick={() => handleToggle(it.recommendation_id)}
                      title="点击展开完整决策详情"
                    >
                      <td style={{ ...tdStyle, whiteSpace: "nowrap", overflowWrap: "normal" }}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", whiteSpace: "nowrap" }}>
                          <span className="history-row__rail" aria-hidden />
                          {expandedId === it.recommendation_id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          <span style={{ display: "inline-flex", flexDirection: "column", gap: 1, whiteSpace: "nowrap", flexShrink: 0 }}>
                            <span style={{ whiteSpace: "nowrap" }}>{it.trade_date}</span>
                            {it.evidence_class === "historical_reconstruction" && (
                              <span
                                className="tag tag--warning"
                                style={{ alignSelf: "flex-start", fontSize: 10, padding: "1px 5px" }}
                                title={`历史重建研究证据${it.reconstruction_batch_id ? ` · 批次 ${it.reconstruction_batch_id}` : ""}${it.actual_generated_at ? ` · 实际生成 ${formatApiDateTime(it.actual_generated_at)}` : ""}；非 LIVE，不计入 LIVE KPI`}
                              >
                                历史重建 · 非 LIVE
                              </span>
                            )}
                            {it.recommended_at && (
                              <span
                                style={{ color: "var(--muted)", fontSize: "var(--text-xs)", fontFamily: "var(--font-mono)", lineHeight: 1.3 }}
                                title={`推荐生成时间（北京时间）：${formatApiDateTime(it.recommended_at)}`}
                              >
                                {formatApiDateTime(it.recommended_at, { hour: "2-digit", minute: "2-digit" })}
                              </span>
                            )}
                          </span>
                        </span>
                      </td>
                      <td style={tdStyle}>
                        <div style={stockCellStyle}>
                          <span style={stockCodeStyle}>{it.code}</span>
                          <span style={stockNameStyle}>{it.name}</span>
                        </div>
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "var(--font-mono)", whiteSpace: "nowrap", overflowWrap: "normal" }}>
                        {it.final_score.toFixed(1)}
                      </td>
                      <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                        <div style={stackStyle}>
                          <span style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>{bucketLabel(it.bucket)}</span>
                          {(() => {
                            const setup = setupLabel(it.setup_type);
                            return setup.isPlaceholder ? (
                              <span
                                title="没有任何策略在此票上命中入场买点，属未触发买点的复盘标记，不建议据此进场"
                                style={{ fontStyle: "italic", opacity: 0.7 }}
                              >
                                {setup.label}
                              </span>
                            ) : (
                              <span title={setup.rawValue ? `原始标识：${setup.rawValue}` : undefined}>{setup.label}</span>
                            );
                          })()}
                          {matchedVersion && (
                            <span title={matchedVersion.version_id} style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}>
                              版本 {shortVersionId(matchedVersion.version_id)}
                            </span>
                          )}
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
                      <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "var(--text-sm)", maxWidth: 360 }}>
                        <div style={stackStyle}>
                          {/* 未结算显示实时状态；已结算只展示当时持久化的最后有效判断。 */}
                          {!settled && (
                            <span
                              style={executionDecisionTagStyle(confirmationLoadingIds.has(it.recommendation_id) ? "loading" : executionDecisionCode(it.recommendation_id, confirmationMap, confirmationErrors))}
                              title={executionDecisionTitle(it.recommendation_id, confirmationMap, confirmationErrors)}
                            >
                              {renderExecutionDecision(it.recommendation_id, confirmationMap, confirmationErrors, confirmationLoadingIds)}
                            </span>
                          )}
                          {holdingDecision && (
                            <span
                              style={executionDecisionTagStyle(holdingDecision.code)}
                              title={holdingDecision.title}
                            >
                              <span>{holdingDecision.text}</span>
                              {holdingDecision.reason && (
                                <span style={{ color: "var(--muted)", fontWeight: 400, overflow: "hidden", textOverflow: "ellipsis" }}>
                                  {clip(holdingDecision.reason, 24)}
                                </span>
                              )}
                            </span>
                          )}
                          {settled && savedExecution && (
                            <span
                              style={executionDecisionTagStyle(savedExecutionDecisionCode(savedExecution))}
                              title={savedExecutionTitle(savedExecution)}
                            >
                              {renderSavedExecution(savedExecution)}
                            </span>
                          )}
                          {savedExecution?.ever_buy_confirmed && (
                            <span
                              style={{ color: "var(--market-up)", fontSize: "var(--text-xs)" }}
                              title="这表示系统曾在有效确认窗口内判断买点成立，不等同于用户已经成交"
                            >
                              曾命中买入条件
                              {savedExecution.first_buy_confirmed_at
                                ? ` · ${formatApiDateTime(savedExecution.first_buy_confirmed_at, { hour: "2-digit", minute: "2-digit" })}`
                                : ""}
                              {savedExecution.first_buy_confirmed_price != null
                                ? ` · ¥${savedExecution.first_buy_confirmed_price.toFixed(2)}`
                                : ""}
                            </span>
                          )}
                          {savedExecution?.cancelled_before_entry && !savedExecution.ever_buy_confirmed && (
                            <span
                              style={{ color: "var(--market-gold, #b8860b)", fontSize: "var(--text-xs)", fontWeight: 600 }}
                              title={savedExecution.first_cancel_reason || "系统在买入条件命中前取消了本次推荐"}
                            >
                              买入前已取消 · 未计入交易胜负
                              {savedExecution.first_cancelled_price != null
                                ? ` · ¥${savedExecution.first_cancelled_price.toFixed(2)}`
                                : ""}
                            </span>
                          )}
                          {savedExecution?.cancelled_before_entry && cancelObservation && (
                            <span
                              style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}
                              title={`假设忽略取消并按首次取消价买入，观察后续交易日收盘；已扣 ${cancelObservation.cost_pct.toFixed(2)}% 成本，非实际收益`}
                            >
                              取消后观察: {cancelObservationText(cancelObservation)}（非实际收益）
                            </span>
                          )}
                          {currentCondition?.label && (
                            <span
                              title={executionConditionTitle(currentCondition)}
                              style={{ color: currentCondition.passed ? "var(--market-up)" : "var(--muted)" }}
                            >
                              {executionConditionPrefix(currentCondition)}: {clip(currentCondition.label, 32)}
                            </span>
                          )}
                          <span
                            title={missingEntryRule ? "这笔旧推荐没有保存可直接执行的买点条件" : it.entry_rule || ""}
                            style={missingEntryRule ? { color: "var(--muted)", fontStyle: "italic" } : undefined}
                          >
                            原始入场计划: {missingEntryRule ? "原推荐未保存明确买点" : clip(it.entry_rule || "—", 32)}
                          </span>
                          <span title={it.invalid_rule || ""}>原始失效条件: {clip(it.invalid_rule || "—", 32)}</span>
                          {keyLevelLabels.length > 0 && (
                            <span
                              title={`规则引用价位：${keyLevelLabels.join(" · ")}`}
                              style={{ color: "var(--accent-strong)", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}
                            >
                              规则引用价位: {keyLevelLabels.join(" · ")}
                            </span>
                          )}
                          {hasUnresolvedKeyLevel && (
                            <span title="这笔旧记录没有保存规则所引用的支撑或平台价位" style={{ color: "var(--market-down)" }}>
                              规则引用价位: 历史快照未留存
                            </span>
                          )}
                        </div>
                      </td>
                      <td style={{ ...tdStyle, color: labelColor, fontWeight: 600 }}>
                        {outcomeLabel(o?.outcome_label, "待标注")}
                      </td>
                      <td
                        style={{
                          ...tdStyle,
                          textAlign: "right",
                          fontFamily: "var(--font-mono)",
                          color: ret == null ? "var(--muted)" : ret >= 0 ? "var(--market-up)" : "var(--market-down)",
                        }}
                      >
                        <div style={{ ...stackStyle, alignItems: "flex-end" }}>
                          {ret != null ? (
                            // 已结算：显示真实净收益（基于实入场口径）
                            <>
                              <span>{`${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%`}</span>
                              {isGapEntry && (
                                <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)", opacity: 0.8 }}>
                                  基于实入场
                                </span>
                              )}
                            </>
                          ) : o?.outcome_label === "missed" ? (
                            // 未触发买点：没有真实成交，展示旁路参考（按推荐价持有到观察期末
                            // 收盘的静态收益，已扣附加成本），让复盘能区分"躲过亏损"与"错过盈利"。
                            // 观察窗口未满的样本不给数值，避免拿截断窗口冒充"持有到期"。
                            <div style={{ ...stackStyle, alignItems: "flex-end" }}>
                              <span>未成交</span>
                              {(() => {
                                const missed = it.missed_observation;
                                if (!missed) return null;
                                if (missed.net_return_pct == null) {
                                  return (
                                    <span
                                      style={{ fontSize: "var(--text-xs)", color: "var(--muted)" }}
                                      title={`观察窗口未满（已观察 ${missed.observed_days}/${missed.horizon_days} 个交易日），参考收益待后续交易日`}
                                    >
                                      参考 观察中 {missed.observed_days}/{missed.horizon_days}日
                                    </span>
                                  );
                                }
                                return (
                                  <span
                                    style={{ fontSize: "var(--text-xs)", color: "var(--muted)" }}
                                    title={`未触发买点、未建仓：假设按推荐价买入并持有到观察期末（${missed.horizon_days} 个交易日）收盘的静态参考收益（已扣 ${missed.cost_pct.toFixed(2)}% 成本）。不代表实际盈亏，只用于判断“没买到”是躲过亏损还是错过盈利。`}
                                  >
                                    参考 {missed.net_return_pct >= 0 ? "+" : ""}{missed.net_return_pct.toFixed(2)}%
                                  </span>
                                );
                              })()}
                            </div>
                          ) : (
                            // 未结算：回落到实时浮盈，标注「未结算」与「收益」区分
                            renderRealtimePnl(pnlMap[it.recommendation_id])
                          )}
                        </div>
                      </td>
                      <td
                        style={{
                          ...tdStyle,
                          color: "var(--muted)",
                          fontSize: "var(--text-sm)",
                          textAlign: "right",
                          minWidth: 128,
                        }}
                      >
                        <div style={{ ...stackStyle, alignItems: "flex-end" }}>
                          <span>
                            {exitReasonLabel(o?.exit_reason, "—", ret)}
                          </span>
                          <span>
                            {o?.holding_days != null ? `持仓 ${o.holding_days} 天` : "—"}
                          </span>
                        </div>
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", width: 44 }}>
                        <Maximize2 size={13} className="history-row__expand" aria-hidden />
                      </td>
                    </tr>
                    {expandedId === it.recommendation_id && (
                      <tr>
                        <td colSpan={10} style={{ padding: 0, borderBottom: "1px solid var(--border)" }}>
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

const stockCellStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  minWidth: 0,
};

const stockCodeStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  whiteSpace: "nowrap",
};

const stockNameStyle: React.CSSProperties = {
  color: "var(--muted)",
  fontSize: "var(--text-sm)",
  whiteSpace: "nowrap",
};

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

const shortVersionId = (value: string) => value.length <= 14 ? value : `${value.slice(0, 14)}…`;

// 状态标签色彩语义（Spec 6.2 表）：buy 涨色积极、wait 警示、cancel 跌色危险、
// not_ready/expired 中性、error 中性带危险描边。
const EXECUTION_DECISION_TONE: Record<string, { color: string; border?: string }> = {
  buy: { color: "var(--market-up)" },
  hold: { color: "var(--market-up)" },
  wait: { color: "var(--market-gold, #b8860b)" },
  reduce: { color: "var(--market-gold, #b8860b)" },
  cancel: { color: "var(--market-down)" },
  exit: { color: "var(--market-down)" },
  observe: { color: "var(--muted)" },
  not_ready: { color: "var(--muted)" },
  expired: { color: "var(--muted)" },
  error: { color: "var(--muted)", border: "1px solid var(--market-down)" },
  loading: { color: "var(--muted)" },
};

function executionDecisionTagStyle(code: string | null): React.CSSProperties {
  const tone = (code && EXECUTION_DECISION_TONE[code]) || EXECUTION_DECISION_TONE.not_ready;
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: "var(--space-2)",
    padding: "1px 6px",
    fontSize: "var(--text-xs)",
    fontWeight: 600,
    borderRadius: 4,
    background: "var(--surface-soft)",
    border: tone.border || "1px solid var(--border)",
    color: tone.color,
    whiteSpace: "nowrap",
  };
}

// 渲染执行规则单元格顶部的实时确认状态（Spec 6.2）：动作语言标签 + 当前价 + 简短原因。
function renderExecutionDecision(
  id: string,
  confirmationMap: Record<string, ExecutionConfirmationItem>,
  confirmationErrors: Record<string, string>,
  loadingIds: Set<string>,
) {
  if (loadingIds.has(id)) {
    return (<><Loader2 size={11} className="spin" /> 实时判断中…</>);
  }
  const errMsg = confirmationErrors[id];
  if (errMsg) {
    return (<>判断失败 <span style={{ color: "var(--muted)", fontWeight: 400 }}>{clip(errMsg, 24)}</span></>);
  }
  const item = confirmationMap[id];
  if (!item) return "等待实时确认";
  if (item.status === "error") {
    return (<>{clip(item.message || "判断失败", 24)}</>);
  }
  const dec = item.entry_decision;
  const label = dec?.label || "等待实时确认";
  const price = item.current_price != null && item.current_price > 0 ? `¥${item.current_price.toFixed(2)}` : "";
  const reason = dec?.reason ? clip(dec.reason, 20) : "";
  const quoteTime = item.quote_time
    ? formatApiDateTime(item.quote_time, { hour: "2-digit", minute: "2-digit" })
    : "";
  return (
    <>
      <span>{label}</span>
      {price && <span style={{ color: "var(--muted)", fontWeight: 400, fontFamily: "var(--font-mono)" }}>{price}</span>}
      {reason && <span style={{ color: "var(--muted)", fontWeight: 400, overflow: "hidden", textOverflow: "ellipsis" }}>{reason}</span>}
      {quoteTime && <span style={{ color: "var(--muted)", fontWeight: 400 }}>截至 {quoteTime}</span>}
    </>
  );
}

function executionDecisionTitle(
  id: string,
  confirmationMap: Record<string, ExecutionConfirmationItem>,
  confirmationErrors: Record<string, string>,
) {
  if (confirmationErrors[id]) return confirmationErrors[id];
  const item = confirmationMap[id];
  if (!item) return "等待实时确认";
  const reason = item.entry_decision?.reason || item.message || "";
  const quoteTime = item.quote_time ? ` · 数据截至 ${formatApiDateTime(item.quote_time)}` : "";
  return `${item.entry_decision?.label || "实时确认"}${reason ? ` · ${reason}` : ""}${quoteTime}`;
}

function savedExecutionDecisionCode(
  state: RecommendationExecutionState,
): "buy" | "wait" | "cancel" | "expired" | null {
  const code = state.latest_decision_code;
  return code === "buy" || code === "wait" || code === "cancel" || code === "expired"
    ? code
    : null;
}

function renderSavedExecution(state: RecommendationExecutionState) {
  const price = state.latest_price != null && state.latest_price > 0
    ? ` · ¥${state.latest_price.toFixed(2)}`
    : "";
  return `最近一次有效判断：${state.latest_decision_label || state.latest_decision_code}${price}`;
}

function savedExecutionTitle(state: RecommendationExecutionState) {
  const checkedAt = state.latest_checked_at
    ? ` · 判断于 ${formatApiDateTime(state.latest_checked_at)}`
    : "";
  return `${state.latest_decision_label || state.latest_decision_code}${state.latest_reason ? ` · ${state.latest_reason}` : ""}${checkedAt}`;
}

function executionConditionPrefix(condition: ExecutionConditionSummary) {
  if (condition.passed) return "当前买入条件已满足";
  // pending：门槛按全日累计量校准，盘中累计值还在增长，收盘前既不算满足也不算不满足
  if (condition.status === "pending") return "当前买入条件待收盘确认";
  return "当前买入判断条件";
}

function executionConditionTitle(condition: ExecutionConditionSummary) {
  const expression = condition.expression ? `规则：${condition.expression}` : "";
  const observed = condition.observed && Object.keys(condition.observed).length > 0
    ? `当前值：${Object.entries(condition.observed).map(([key, value]) => `${key}=${String(value)}`).join("，")}`
    : "";
  return [condition.label || "买点判断条件", expression, observed].filter(Boolean).join(" · ");
}

// 实时盈亏单元格（未结算占位）：holding 显示浮盈%（红涨绿跌），watching 显示距买点，
// waiting 显示待下一交易日入场，无数据显示 —。统一标注「未结算」，与已结算收益区分。
const renderRealtimePnl = (item: RealtimePnlItem | undefined) => {
  const unsettledTag = (
    <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)", opacity: 0.8 }}>未结算</span>
  );
  if (!item) return <span style={{ color: "var(--muted)" }}>—</span>;
  if (item.status === "holding" && item.pnl_pct != null) {
    const up = item.pnl_pct >= 0;
    return (
      <>
        <span style={{ color: up ? "var(--market-up)" : "var(--market-down)", fontWeight: 600 }} title={`入场 ${item.entry_price} · 现价 ${item.now_price}`}>
          {up ? "+" : ""}{item.pnl_pct.toFixed(2)}%
        </span>
        {unsettledTag}
      </>
    );
  }
  if (item.status === "watching") {
    const dist = item.distance_to_target_pct;
    return (
      <>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }} title={item.reason || "买点未触发"}>
          距买点 {dist != null ? `${dist >= 0 ? "+" : ""}${dist.toFixed(1)}%` : "—"}
        </span>
        {unsettledTag}
      </>
    );
  }
  if (item.status === "waiting") {
    return (
      <>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }} title={item.reason}>
          待下一交易日入场
        </span>
        {unsettledTag}
      </>
    );
  }
  return <span style={{ color: "var(--muted)" }} title={item.reason || "实时行情不可用"}>—</span>;
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  tableLayout: "auto",
  borderCollapse: "collapse",
  fontSize: "var(--text-base)",
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "var(--space-2)",
  color: "var(--muted)",
  fontWeight: 500,
  fontSize: "var(--text-sm)",
  whiteSpace: "nowrap",
  borderBottom: "1px solid var(--border)",
};

const tdStyle: React.CSSProperties = {
  padding: "var(--space-2)",
  borderBottom: "1px solid var(--border)",
  overflowWrap: "break-word",
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
