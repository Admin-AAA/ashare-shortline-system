import {
  ArrowLeft,
  BarChart3,
  Calendar,
  ChevronDown,
  ChevronRight,
  Filter,
  Layers,
  Loader2,
  Maximize2,
  RefreshCcw,
  RotateCcw,
  Search,
  Target,
  TrendingDown,
  TrendingUp,
  Zap,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Panel } from "../components/Panel";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";
import { RecommendationDetailCard } from "../components/RecommendationDetailCard";
import { fmtPrice } from "../components/Shared";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";
import { api } from "../api/client";
import type { RecommendationHistoryResponse, RealtimePnlItem, ExecutionConfirmationResponse, ExecutionConfirmationItem, RecommendationExecutionState } from "../types";
import {
  BUCKET_LABELS,
  bucketLabel,
  exitReasonLabel,
  formatApiDateTime,
  isOutcomeSettled,
  outcomeLabel,
  outcomeTone,
  setupLabel,
} from "../utils/display";
import {
  cancelObservationText,
  collectUnsettledIds,
  executionDecisionCode,
  isPlaceholderEntryRule,
  type ExecutionDecisionCode,
} from "../utils/executionConfirmation";
import { ruleKeyLevelLabels, rulesMentionKeyLevel } from "../utils/ruleKeyLevels";
import { generateMockHistory, holdingDecisionDefaultLabel, holdingDecisionTone, type DemoHistoryItem } from "./historyDemoData";

/**
 * Demo A —— 量化仪表盘 + 卡片列表
 * 适合想一眼看清「当前策略赚了多少、胜率如何、哪些票在持仓」的用户。
 * 信息层级：KPI → 过滤器 → 卡片流；卡片内再分层为股票/策略/收益三块。
 *
 * 新增：实时持仓/卖出决策（holding_decision）与 entry_decision 并列展示，
 * 未持有看买点，已持有看卖点，使用同一份实时行情。
 */
export function HistoryPageDemoA() {
  const { navigate } = useUrlRoute();
  const strategyId = queryParam("strategy_id");
  const strategyName = queryParam("strategy_name");
  const strategyVersionId = queryParam("strategy_version_id");
  const strategyVersion = queryParam("strategy_version");
  const [label, setLabel] = useState<"" | "success" | "failed" | "missed" | "pending">("");
  const [datePreset, setDatePreset] = useState<"7d" | "30d" | "month" | "all" | "custom">("30d");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [codeInput, setCodeInput] = useState("");
  const [codeQuery, setCodeQuery] = useState("");
  const [bucket, setBucket] = useState("");
  const [setupType, setSetupType] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [fullDetailId, setFullDetailId] = useState<string | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<Record<string, string>>({});
  const [details, setDetails] = useState<Record<string, import("../types").RecommendationDetail>>({});
  // 分页
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const [showStats, setShowStats] = useState(true);
  // 实时买入/持仓确认状态（与原 HistoryPage 同源）
  const [confirmationMap, setConfirmationMap] = useState<Record<string, ExecutionConfirmationItem>>({});
  const [confirmationErrors, setConfirmationErrors] = useState<Record<string, string>>({});
  const [confirmationLoadingIds, setConfirmationLoadingIds] = useState<Set<string>>(new Set());
  const [confirmationComputedAt, setConfirmationComputedAt] = useState<string | null>(null);
  const [pnlMap, setPnlMap] = useState<Record<string, RealtimePnlItem>>({});
  const confirmationReqRef = useRef(0);

  // 优先尝试真实接口，失败则回退到 mock（让 demo 在无后端时也能看）
  const { data: realData, loading, error } = useApi<RecommendationHistoryResponse>(
    async () => api.recommendationHistory({ limit: 100 }),
    [],
  );
  const data = realData ?? (error ? generateMockHistory(60) : undefined);

  const effectiveRange = useMemo(() => {
    if (datePreset === "custom") return { from: dateFrom, to: dateTo };
    if (datePreset === "all") return { from: "", to: "" };
    const today = new Date();
    const to = today.toISOString().slice(0, 10);
    const from = new Date(today);
    if (datePreset === "7d") from.setDate(from.getDate() - 6);
    else if (datePreset === "30d") from.setDate(from.getDate() - 29);
    else if (datePreset === "month") from.setDate(1);
    return { from: from.toISOString().slice(0, 10), to };
  }, [datePreset, dateFrom, dateTo]);

  const resetPage = () => setPage(0);

  const filteredItems = useMemo(() => {
    if (!data) return [];
    return data.items.filter((it) => {
      if (label && it.outcome?.outcome_label !== label) return false;
      if (codeQuery && !it.code.includes(codeQuery)) return false;
      if (bucket && it.bucket !== bucket) return false;
      if (setupType && it.setup_type !== setupType) return false;
      if (effectiveRange.from && it.trade_date < effectiveRange.from) return false;
      if (effectiveRange.to && it.trade_date > effectiveRange.to) return false;
      return true;
    });
  }, [data, label, codeQuery, bucket, setupType, effectiveRange]);

  const total = filteredItems.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pageItems = filteredItems.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const summary = useMemo(() => {
    const trade = filteredItems.filter((it) => ["success", "failed"].includes(it.outcome?.outcome_label ?? ""));
    const wins = trade.filter((it) => it.outcome?.outcome_label === "success");
    const returns = trade.map((it) => it.outcome?.executed_return ?? 0);
    const avg = returns.length ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
    const total = returns.reduce((a, b) => a + b, 0);
    return {
      tradeCount: trade.length,
      winCount: wins.length,
      winRate: trade.length ? (wins.length / trade.length) * 100 : 0,
      avgReturn: avg - 0.07,
      totalReturn: total - trade.length * 0.07,
      pendingCount: filteredItems.filter((it) => it.outcome?.outcome_label === "pending").length,
      holdingCount: filteredItems.filter((it) => it.outcome?.outcome_label === "provisional").length,
    };
  }, [filteredItems]);

  const setupOptions = useMemo(
    () =>
      (data?.facets?.setup_types ?? [])
        .map((value) => ({ value, label: setupLabel(value).label }))
        .sort((a, b) => a.label.localeCompare(b.label, "zh-CN")),
    [data?.facets?.setup_types],
  );

  const resetFilters = () => {
    setLabel("");
    setDatePreset("30d");
    setDateFrom("");
    setDateTo("");
    setCodeInput("");
    setCodeQuery("");
    setBucket("");
    setSetupType("");
    resetPage();
  };

  // 当前页未结算推荐 ID（去重 + 排序成稳定 key，避免重渲染重复请求）
  const unsettledIds = useMemo(() => collectUnsettledIds(pageItems), [pageItems]);
  const unsettledIdsKey = unsettledIds.join(",");

  // 批量买入/持仓确认：自动加载 + 手动刷新共用。只有存在未结算行时才请求。
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
        // 后端批量确认接口；mock 模式下自动从 item.holding_decision 构造
        const res: ExecutionConfirmationResponse = error
          ? buildMockConfirmations(ids, pageItems)
          : await api.confirmExecutionBatch(ids, refresh);
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
    [error, pageItems],
  );

  useEffect(() => {
    loadExecutionConfirmations(unsettledIds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unsettledIdsKey, loadExecutionConfirmations]);

  const refreshRealtimeState = useCallback(async () => {
    await loadExecutionConfirmations(unsettledIds, true);
  }, [loadExecutionConfirmations, unsettledIds]);

  if (loading) return <PageSkeleton title="复盘" hint="加载推荐历史" rowCount={8} />;
  if (!data) return <ErrorPage title="历史加载失败" message={error || "无数据"} />;

  return (
    <Panel
      kicker="重构 Demo A"
      title="量化仪表盘视图"
      actions={
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-base)", display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
          <span>
            共 {total} 笔 · 当前 {total === 0 ? 0 : safePage * pageSize + 1}-{Math.min((safePage + 1) * pageSize, total)}
          </span>
          <button className="btn btn--ghost" onClick={() => navigate("/history")}>
            <ArrowLeft size={14} /> 返回原复盘页
          </button>
        </span>
      }
    >
      {/* ── 策略下钻横幅 ── */}
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
          <span className="text-muted">{strategyVersionId ? "直接参与评分的正式推荐" : "的推荐历史"}</span>
          {strategyVersionId && (
            <span className="tag" title={strategyVersionId} style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}>
              v{strategyVersion || "历史"} · {strategyVersionId.length <= 14 ? strategyVersionId : `${strategyVersionId.slice(0, 14)}…`}
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

      {/* ── KPI 概览卡 ── */}
      <div className="hdemo-kpi-grid">
        <KpiCard
          icon={<Target size={18} />}
          label="胜率"
          value={`${summary.winRate.toFixed(1)}%`}
          sub={`${summary.winCount}/${summary.tradeCount} 笔已成交`}
          tone={summary.winRate >= 60 ? "up" : summary.winRate >= 40 ? "warn" : "down"}
        />
        <KpiCard
          icon={<TrendingUp size={18} />}
          label="平均单笔净收益"
          value={`${summary.avgReturn >= 0 ? "+" : ""}${summary.avgReturn.toFixed(2)}%`}
          sub="已扣 0.07% 交易成本"
          tone={summary.avgReturn >= 0 ? "up" : "down"}
        />
        <KpiCard
          icon={<BarChart3 size={18} />}
          label="累计单笔净收益"
          value={`${summary.totalReturn >= 0 ? "+" : ""}${summary.totalReturn.toFixed(2)}%`}
          sub="非复利账户收益"
          tone={summary.totalReturn >= 0 ? "up" : "down"}
        />
        <KpiCard
          icon={<Zap size={18} />}
          label="实时/待结算"
          value={`${summary.pendingCount + summary.holdingCount}`}
          sub={`待标注 ${summary.pendingCount} · 结算中 ${summary.holdingCount}`}
          tone="neutral"
        />
      </div>

      {/* ── 统计详情（可折叠，保留原页面筛选栏徽标信息） ── */}
      <StatsCollapse
        summary={data.summary}
        total={total}
        isOpen={showStats}
        onToggle={() => setShowStats((s) => !s)}
      />

      {/* ── 过滤器 ── */}
      <div className="hdemo-filterbar">
        <div className="hdemo-filterbar__section">
          <Filter size={13} className="text-muted" />
          <span className="hdemo-filterbar__title">筛选</span>
          <ChipGroup
            options={[
              { key: "7d", label: "近7天" },
              { key: "30d", label: "近30天" },
              { key: "month", label: "本月" },
              { key: "all", label: "全部" },
              { key: "custom", label: "自定义" },
            ]}
            value={datePreset}
            onChange={(v) => {
              setDatePreset(v as typeof datePreset);
              resetPage();
            }}
          />
          {datePreset === "custom" && (
            <>
              <input
                className="cfg-input records-filterbar__date"
                type="date"
                value={dateFrom}
                onChange={(e) => { setDateFrom(e.target.value); resetPage(); }}
              />
              <span className="records-filterbar__arrow">→</span>
              <input
                className="cfg-input records-filterbar__date"
                type="date"
                value={dateTo}
                onChange={(e) => { setDateTo(e.target.value); resetPage(); }}
              />
            </>
          )}
        </div>

        <div className="hdemo-filterbar__section">
          <select className="cfg-input hdemo-select" value={label} onChange={(e) => { setLabel(e.target.value as typeof label); resetPage(); }}>
            <option value="">全部结果</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="missed">未触发</option>
            <option value="pending">待标注</option>
          </select>

          <input
            className="cfg-input hdemo-input"
            placeholder="股票代码"
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setCodeQuery(codeInput.trim())}
          />

          <select className="cfg-input hdemo-select" value={bucket} onChange={(e) => { setBucket(e.target.value); resetPage(); }}>
            <option value="">全部策略桶</option>
            {Object.entries(BUCKET_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>

          <select className="cfg-input hdemo-select" value={setupType} onChange={(e) => { setSetupType(e.target.value); resetPage(); }}>
            <option value="">全部买点</option>
            {setupOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        <div className="hdemo-filterbar__actions">
          <button
            className="btn btn--ghost"
            onClick={refreshRealtimeState}
            disabled={confirmationLoadingIds.size > 0 || unsettledIds.length === 0}
            title={
              confirmationComputedAt
                ? `未结算推荐实时盈亏与买入/持仓确认 · 上次 ${confirmationComputedAt}`
                : "未结算推荐实时盈亏与买入/持仓确认"
            }
          >
            {confirmationLoadingIds.size > 0 ? <Loader2 size={13} className="spin" /> : <Zap size={13} />}
            刷新实时状态
          </button>
          <button className="btn btn--primary" onClick={() => setCodeQuery(codeInput.trim())}>
            <Search size={13} /> 查询
          </button>
          <button className="btn btn--ghost" onClick={resetFilters}>
            <RotateCcw size={13} /> 重置
          </button>
        </div>
      </div>

      {/* ── 结果计数 ── */}
      <div className="hdemo-meta">
        <Calendar size={13} className="text-muted" />
        <span>
          共 <strong>{total}</strong> 笔推荐 · 当前筛选范围
        </span>
      </div>

      {/* ── 卡片列表 ── */}
      {pageItems.length === 0 ? (
        <EmptyState icon={<Search size={32} />} title="无匹配推荐" reason="尝试放宽筛选条件" />
      ) : (
        <div className="hdemo-card-list">
          {pageItems.map((it) => {
            const o = it.outcome;
            const settled = isOutcomeSettled(o?.outcome_label);
            const tone = outcomeTone(o?.outcome_label);
            const ret = settled ? o?.executed_return : undefined;
            const up = ret != null && ret >= 0;
            const expanded = expandedId === it.recommendation_id;
            const matchedVersion = it.strategy_versions?.find((version) => version.strategy_id === strategyId);
            const setup = setupLabel(it.setup_type);
            const recPrice = it.recommend_price;
            const overnightGap = o?.entry_price != null && recPrice != null && recPrice > 0
              ? (o.entry_price - recPrice) / recPrice * 100
              : null;
            const isGapEntry = overnightGap != null && Math.abs(overnightGap) > 3;
            const confirmation = confirmationMap[it.recommendation_id];
            const quoteTime = confirmation?.quote_time
              ? formatApiDateTime(confirmation.quote_time, { hour: "2-digit", minute: "2-digit" })
              : confirmationComputedAt
                ? `上次 ${confirmationComputedAt}`
                : null;
            return (
              <Fragment key={it.recommendation_id}>
                <div
                  className={`hdemo-card${expanded ? " hdemo-card--expanded" : ""}`}
                  onClick={() => setExpandedId(expanded ? null : it.recommendation_id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setExpandedId(expanded ? null : it.recommendation_id)}
                >
                  {/* 左侧：股票与日期 */}
                  <div className="hdemo-card__left">
                    <div className="hdemo-card__stock">
                      <span className="hdemo-card__code">{it.code}</span>
                      <span className="hdemo-card__name">{it.name}</span>
                    </div>
                    <div className="hdemo-card__date">
                      <Calendar size={12} />
                      {it.trade_date}
                      {it.recommended_at && (
                        <span className="hdemo-card__time">
                          {formatApiDateTime(it.recommended_at, { hour: "2-digit", minute: "2-digit" })}
                        </span>
                      )}
                    </div>
                    <div className="hdemo-card__score">
                      <span className="hdemo-card__score-label">评分</span>
                      <span className="hdemo-card__score-value">{it.final_score.toFixed(1)}</span>
                    </div>
                  </div>

                  {/* 中间：策略与价格 */}
                  <div className="hdemo-card__center">
                    <div className="hdemo-card__tags">
                      <span className="hdemo-tag hdemo-tag--bucket">
                        <Layers size={11} />
                        {bucketLabel(it.bucket)}
                      </span>
                      {setup.isPlaceholder ? (
                        <span className="hdemo-tag hdemo-tag--setup" title="没有任何策略在此票上命中入场买点，不建议据此进场">
                          <Target size={11} />
                          {setup.label}
                        </span>
                      ) : (
                        <span className="hdemo-tag hdemo-tag--setup" title={setup.rawValue ? `原始标识：${setup.rawValue}` : undefined}>
                          <Target size={11} />
                          {setup.label}
                        </span>
                      )}
                      {matchedVersion && (
                        <span className="hdemo-tag hdemo-tag--version" title={matchedVersion.version_id}>
                          v{strategyVersion || "历史"} · {matchedVersion.version_id.length <= 14 ? matchedVersion.version_id : `${matchedVersion.version_id.slice(0, 14)}…`}
                        </span>
                      )}
                      {it.risk_level && (
                        <span className={`hdemo-tag hdemo-tag--risk hdemo-tag--risk-${it.risk_level}`}>
                          {it.risk_level === "low" ? "低风险" : it.risk_level === "medium" ? "中风险" : it.risk_level === "high" ? "高风险" : "风险待确认"}
                        </span>
                      )}
                    </div>
                    <div className="hdemo-card__prices">
                      {isGapEntry && o?.entry_price != null ? (
                        <>
                          <PriceItem label="实入场" value={fmtPrice(o.entry_price)} />
                          <PriceItem label="实止损" value={fmtPrice(o.stop_loss_price)} />
                          <PriceItem label="实止盈" value={fmtPrice(o.take_profit_price)} />
                          <span className="hdemo-price-note" title={`推荐价 ${fmtPrice(recPrice)}，隔夜跳空 ${overnightGap!.toFixed(1)}%`}>
                            跳空 {overnightGap! >= 0 ? "+" : ""}{overnightGap!.toFixed(1)}%
                          </span>
                        </>
                      ) : (
                        <>
                          <PriceItem label="推荐价" value={fmtPrice(it.recommend_price)} />
                          <PriceItem label="理想买" value={fmtPrice(it.ideal_buy)} />
                          <PriceItem label="次买" value={fmtPrice(it.secondary_buy)} />
                          <PriceItem label="止损" value={fmtPrice(it.stop_loss)} />
                          <PriceItem label="止盈" value={fmtPrice(it.take_profit)} />
                        </>
                      )}
                    </div>
                    <div className="hdemo-card__rule" title={it.entry_rule || undefined}>
                      入场：{isPlaceholderEntryRule(it.entry_rule) ? "原推荐未保存明确买点" : clip(it.entry_rule || "—", 40)}
                    </div>
                  </div>

                  {/* 右侧：结果 + 实时决策面板 */}
                  <div className="hdemo-card__right">
                    <div className="hdemo-card__right-top">
                      <span className={`hdemo-result hdemo-result--${tone}`}>
                        {outcomeLabel(o?.outcome_label)}
                      </span>
                      <div className={`hdemo-return ${up ? "hdemo-return--up" : ret != null ? "hdemo-return--down" : ""}`}>
                        {ret != null ? (
                          <>
                            {up ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
                            <span>{`${up ? "+" : ""}${ret.toFixed(2)}%`}</span>
                          </>
                        ) : (
                          <>{renderRealtimePnl(pnlMap[it.recommendation_id])}</>
                        )}
                      </div>
                    </div>

                    <RealtimePanel
                      id={it.recommendation_id}
                      settled={settled}
                      savedExecution={it.execution_state}
                      confirmationMap={confirmationMap}
                      confirmationErrors={confirmationErrors}
                      confirmationLoadingIds={confirmationLoadingIds}
                      holdingDecision={getHoldingDecision(it)}
                      currentPrice={confirmation?.current_price}
                      stopLoss={it.stop_loss}
                      takeProfit={it.take_profit}
                    />

                    <div className="hdemo-card__exit">
                      {exitReasonLabel(o?.exit_reason, "—", ret)}
                      {o?.holding_days != null && ` · ${o.holding_days} 天`}
                      {isGapEntry && <span className="hdemo-return-note">基于实入场</span>}
                    </div>

                    {quoteTime && <div className="hdemo-card__quote-time">数据截至 {quoteTime}</div>}

                    <div className="hdemo-card__expand-icon">
                      {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </div>
                  </div>
                </div>

                {expanded && (
                  <div className="hdemo-card__detail">
                    <div className="hdemo-detail-grid hdemo-detail-grid--4">
                      <DetailBlock title="风控与价格">
                        <Row label="推荐价" value={fmtPrice(it.recommend_price)} />
                        <Row label="理想买" value={fmtPrice(it.ideal_buy)} />
                        <Row label="次买" value={fmtPrice(it.secondary_buy)} />
                        <Row label="止损价" value={fmtPrice(it.stop_loss)} />
                        <Row label="止盈价" value={fmtPrice(it.take_profit)} />
                        <Row label="失效条件" value={it.invalid_rule || "—"} />
                        <KeyLevelsRow item={it} />
                      </DetailBlock>

                      <DetailBlock title="买入判断（实时）">
                        <Row
                          label="当前状态"
                          value={
                            <ExecutionDecisionValue
                              id={it.recommendation_id}
                              settled={settled}
                              savedExecution={it.execution_state}
                              confirmationMap={confirmationMap}
                              confirmationErrors={confirmationErrors}
                              confirmationLoadingIds={confirmationLoadingIds}
                            />
                          }
                        />
                        <Row
                          label="当前条件"
                          value={
                            <ConditionSummaryValue
                              settled={settled}
                              savedExecution={it.execution_state}
                              confirmationMap={confirmationMap}
                              id={it.recommendation_id}
                            />
                          }
                        />
                        <Row
                          label="曾命中买点"
                          value={it.execution_state?.ever_buy_confirmed ? `是 · ¥${it.execution_state.first_buy_confirmed_price?.toFixed(2)}` : "否"}
                        />
                        <Row label="原始入场计划" value={it.entry_rule || "—"} />
                      </DetailBlock>

                      <DetailBlock title="持仓/卖出判断（实时）">
                        <Row
                          label="当前状态"
                          value={
                            <HoldingDecisionValue
                              id={it.recommendation_id}
                              confirmationMap={confirmationMap}
                              fallback={getHoldingDecision(it)}
                            />
                          }
                        />
                        <Row
                          label="持仓原因"
                          value={getHoldingDecision(it)?.reason || confirmationMap[it.recommendation_id]?.holding_decision?.reason || "—"}
                        />
                        <Row
                          label="买入前已取消"
                          value={
                            it.execution_state?.cancelled_before_entry
                              ? `是${it.execution_state.first_cancelled_price != null ? ` · ¥${it.execution_state.first_cancelled_price.toFixed(2)}` : ""}`
                              : "否"
                          }
                        />
                        <Row
                          label="取消后观察"
                          value={it.cancel_observation ? cancelObservationText(it.cancel_observation) : "—"}
                        />
                      </DetailBlock>

                      <DetailBlock title="信号质量">
                        <Row label="LLM 态度" value={it.llm_stance || "—"} />
                        <Row label="LLM Δ" value={it.llm_delta != null ? it.llm_delta.toFixed(2) : "—"} />
                        <Row label="共识分" value={it.consensus_score != null ? it.consensus_score.toFixed(1) : "—"} />
                        <Row label="风险等级" value={it.risk_level || "—"} />
                      </DetailBlock>
                    </div>
                    <div className="hdemo-detail-footer">
                      <button
                        className="btn btn--ghost"
                        onClick={async (e) => {
                          e.stopPropagation();
                          const showing = fullDetailId === it.recommendation_id;
                          if (showing) {
                            setFullDetailId(null);
                            return;
                          }
                          setFullDetailId(it.recommendation_id);
                          if (details[it.recommendation_id]) return;
                          setDetailLoadingId(it.recommendation_id);
                          setDetailError((prev) => ({ ...prev, [it.recommendation_id]: "" }));
                          try {
                            const detail = await api.recommendationDetail(it.recommendation_id);
                            setDetails((prev) => ({ ...prev, [it.recommendation_id]: detail }));
                          } catch (e) {
                            setDetailError((prev) => ({ ...prev, [it.recommendation_id]: (e as Error).message || "加载失败" }));
                          } finally {
                            setDetailLoadingId(null);
                          }
                        }}
                      >
                        <Maximize2 size={13} /> {fullDetailId === it.recommendation_id ? "收起完整详情" : "查看完整决策详情"}
                      </button>
                      <button className="btn btn--ghost" onClick={(e) => { e.stopPropagation(); refreshRealtimeState(); }}>
                        <RefreshCcw size={13} /> 刷新实时状态
                      </button>
                    </div>

                    {fullDetailId === it.recommendation_id && (
                      <div className="hdemo-full-detail" onClick={(e) => e.stopPropagation()}>
                        {detailLoadingId === it.recommendation_id ? (
                          <div className="hdemo-detail-loading"><Loader2 size={16} className="spin" /> 加载完整决策详情…</div>
                        ) : detailError[it.recommendation_id] ? (
                          <div className="hdemo-detail-loading" style={{ color: "var(--market-down)" }}>{detailError[it.recommendation_id]}</div>
                        ) : (
                          <RecommendationDetailCard item={details[it.recommendation_id] || it} />
                        )}
                      </div>
                    )}
                  </div>
                )}
              </Fragment>
            );
          })}

          {/* ── 分页 ── */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-3)",
              padding: "var(--space-3) 0",
              borderTop: "1px solid var(--border)",
            }}
          >
            <button
              className="btn btn--ghost"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
              style={{ opacity: safePage === 0 ? 0.5 : 1 }}
            >
              上一页
            </button>
            <span style={{ fontSize: "var(--text-base)", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
              {safePage + 1} / {totalPages}
            </span>
            <button
              className="btn btn--ghost"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={safePage >= totalPages - 1}
              style={{ opacity: safePage >= totalPages - 1 ? 0.5 : 1 }}
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </Panel>
  );
}

function StatsCollapse({
  summary,
  total,
  isOpen,
  onToggle,
}: {
  summary: RecommendationHistoryResponse["summary"] | undefined;
  total: number;
  isOpen: boolean;
  onToggle: () => void;
}) {
  if (!summary) return null;
  const rate = summary.win_rate ?? 0;
  const tier = rate >= 60 ? "high" : rate >= 40 ? "mid" : "low";
  const cancel = summary.cancel_observation;
  return (
    <div className="hdemo-stats-collapse">
      <button className="hdemo-stats-collapse__toggle" onClick={onToggle}>
        <BarChart3 size={14} />
        <span>筛选范围统计</span>
        {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {isOpen && (
        <div className="hdemo-stats-collapse__body">
          <div className={`winrate-badge winrate-badge--${tier}`} title={`当前筛选范围净胜率：${summary.win_count}/${summary.trade_count} 笔已成交正式推荐`}>
            <span className="winrate-badge__label">胜率</span>
            <span className="winrate-badge__fraction">{summary.win_count}/{summary.trade_count}</span>
            <div className="winrate-badge__bar"><div className="winrate-bar__fill" style={{ width: `${rate}%` }} /></div>
            <span className="winrate-badge__value">{rate.toFixed(1)}%</span>
          </div>

          {summary.average_net_return != null && (
            <div className={`return-badge return-badge--${summary.average_net_return >= 0 ? "up" : "down"}`} title={`当前筛选范围 ${summary.trade_count} 笔已成交正式推荐的平均单笔净收益，已扣 ${summary.additional_cost_pct.toFixed(2)}% 交易成本`}>
              <span className="return-badge__label">平均单笔净收益</span>
              <span className="return-badge__value">{summary.average_net_return >= 0 ? "+" : ""}{summary.average_net_return.toFixed(2)}%</span>
            </div>
          )}

          {summary.trade_count > 0 && (
            <div className={`return-badge return-badge--${summary.total_net_return >= 0 ? "up" : "down"}`} title="当前筛选范围所有已成交正式推荐的单笔净收益之和；不是账户复利收益率">
              <span className="return-badge__label">累计单笔净收益</span>
              <span className="return-badge__value">{summary.total_net_return >= 0 ? "+" : ""}{summary.total_net_return.toFixed(2)}%</span>
            </div>
          )}

          <div className="return-badge" title="当前筛选范围内正式推荐总数">
            <span className="return-badge__label">匹配笔数</span>
            <span className="return-badge__value">{total}</span>
          </div>

          {cancel && cancel.cancelled_count > 0 && (
            <>
              <div className="return-badge" title="买入条件从未命中、随后被系统取消的推荐；这些票不进入交易成功率、失败率或实际交易收益">
                <span className="return-badge__label">买入前取消</span>
                <span className="return-badge__value">{cancel.cancelled_count} 笔</span>
              </div>

              {cancel.average_5d_return != null && (
                <div className={`return-badge return-badge--${cancel.average_5d_return >= 0 ? "up" : "down"}`} title={`假设忽略取消并按首次取消价买入，观察后第 5 个交易日收盘的平均净收益；共 ${cancel.observed_5d_count} 笔，非实际收益`}>
                  <span className="return-badge__label">取消后 5 日观察</span>
                  <span className="return-badge__value">{cancel.average_5d_return >= 0 ? "+" : ""}{cancel.average_5d_return.toFixed(2)}%</span>
                </div>
              )}

              {cancel.avoided_loss_rate_5d != null && (
                <div className="return-badge" title="取消后第 5 个交易日假设净收益小于或等于 0 的比例">
                  <span className="return-badge__label">5 日避免亏损率</span>
                  <span className="return-badge__value">{cancel.avoided_loss_rate_5d.toFixed(1)}%</span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function KpiCard({
  icon,
  label,
  value,
  sub,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  tone: "up" | "down" | "warn" | "neutral";
}) {
  return (
    <div className={`hdemo-kpi hdemo-kpi--${tone}`}>
      <div className="hdemo-kpi__top">
        <span className="hdemo-kpi__icon">{icon}</span>
        <span className="hdemo-kpi__label">{label}</span>
      </div>
      <div className="hdemo-kpi__value">{value}</div>
      <div className="hdemo-kpi__sub">{sub}</div>
    </div>
  );
}

function ChipGroup<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { key: T; label: string }[];
  value: T;
  onChange: (key: T) => void;
}) {
  return (
    <div className="hdemo-chipgroup">
      {options.map((opt) => (
        <button
          key={opt.key}
          className={`hdemo-chip${value === opt.key ? " hdemo-chip--active" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            onChange(opt.key);
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function PriceItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="hdemo-price-item">
      <span className="hdemo-price-item__label">{label}</span>
      <span className="hdemo-price-item__value">{value}</span>
    </div>
  );
}

function DetailBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="hdemo-detail-block">
      <div className="hdemo-detail-block__title">{title}</div>
      <div className="hdemo-detail-block__body">{children}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="hdemo-detail-row">
      <span className="hdemo-detail-row__label">{label}</span>
      <span className="hdemo-detail-row__value">{value}</span>
    </div>
  );
}

function getHoldingDecision(item: RecommendationHistoryResponse["items"][number]) {
  return (item as DemoHistoryItem).holding_decision;
}

function RealtimePanel({
  id,
  settled,
  savedExecution,
  confirmationMap,
  confirmationErrors,
  confirmationLoadingIds,
  holdingDecision,
  currentPrice,
  stopLoss,
  takeProfit,
}: {
  id: string;
  settled: boolean;
  savedExecution: RecommendationExecutionState | null;
  confirmationMap: Record<string, ExecutionConfirmationItem>;
  confirmationErrors: Record<string, string>;
  confirmationLoadingIds: Set<string>;
  holdingDecision?: { code?: string; label?: string; reason?: string } | null;
  currentPrice?: number | null;
  stopLoss?: number | null;
  takeProfit?: number | null;
}) {
  const confirmation = confirmationMap[id];
  const entryCode = confirmationLoadingIds.has(id)
    ? "loading"
    : executionDecisionCode(id, confirmationMap, confirmationErrors);
  const entryItem = confirmationMap[id];
  const entryLabel = entryItem?.status === "error"
    ? clip(entryItem.message || "判断失败", 18)
    : entryItem?.entry_decision?.label || (entryCode ? undefined : "等待实时确认");
  const entryReason = entryItem?.entry_decision?.reason;

  const holdingCode = holdingDecision?.code || confirmation?.holding_decision?.code;
  const holdingLabel = holdingDecision?.label || confirmation?.holding_decision?.label;
  const holdingReason = holdingDecision?.reason || confirmation?.holding_decision?.reason;
  const holdingTone = holdingDecisionTone(holdingCode);

  return (
    <div className="hdemo-realtime-panel">
      <div className="hdemo-realtime-panel__title">实时判断</div>

      {/* 买入判断 */}
      <div className="hdemo-realtime-panel__row">
        <span className="hdemo-realtime-panel__label">买</span>
        {settled && savedExecution ? (
          <span style={executionDecisionTagStyle(savedExecutionDecisionCode(savedExecution))} title={savedExecutionTitle(savedExecution)}>
            {renderSavedExecution(savedExecution)}
          </span>
        ) : (
          <span style={executionDecisionTagStyle(entryCode)} title={executionDecisionTitle(id, confirmationMap, confirmationErrors)}>
            {confirmationLoadingIds.has(id) ? <><Loader2 size={11} className="spin" /> 实时判断中…</> : entryLabel}
          </span>
        )}
      </div>
      {!settled && entryReason && (
        <div className="hdemo-realtime-panel__reason" title={entryReason}>{clip(entryReason, 24)}</div>
      )}

      {/* 持仓/卖出判断 */}
      {holdingCode && (
        <div className="hdemo-realtime-panel__row">
          <span className="hdemo-realtime-panel__label">持</span>
          <span className={`hdemo-realtime-panel__badge hdemo-realtime-panel__badge--${holdingTone}`} title={holdingReason || undefined}>
            {holdingLabel || holdingDecisionDefaultLabel(holdingCode)}
          </span>
        </div>
      )}
      {holdingReason && (
        <div className="hdemo-realtime-panel__reason" title={holdingReason}>{clip(holdingReason, 24)}</div>
      )}

      {/* 价格参考 */}
      <div className="hdemo-realtime-panel__prices">
        <span title="当前价/最新判断价">现 {currentPrice != null && currentPrice > 0 ? `¥${currentPrice.toFixed(2)}` : "—"}</span>
        <span>止 {stopLoss != null ? stopLoss.toFixed(2) : "—"}</span>
        <span>盈 {takeProfit != null ? takeProfit.toFixed(2) : "—"}</span>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
// 执行规则 / 实时决策展示组件（从原 HistoryPage 提取并适配到 Demo A 卡片详情）
// ═══════════════════════════════════════════════════════════════

function KeyLevelsRow({ item }: { item: RecommendationHistoryResponse["items"][number] }) {
  const labels = ruleKeyLevelLabels(item.entry_rule, item.invalid_rule, item.key_levels);
  const hasUnresolved = labels.length === 0 && rulesMentionKeyLevel(item.entry_rule, item.invalid_rule);
  if (labels.length > 0) {
    return <Row label="规则引用价位" value={labels.join(" · ")} />;
  }
  if (hasUnresolved) {
    return <Row label="规则引用价位" value="历史快照未留存" />;
  }
  return null;
}

function ExecutionDecisionValue({
  id,
  settled,
  savedExecution,
  confirmationMap,
  confirmationErrors,
  confirmationLoadingIds,
}: {
  id: string;
  settled: boolean;
  savedExecution: RecommendationExecutionState | null;
  confirmationMap: Record<string, ExecutionConfirmationItem>;
  confirmationErrors: Record<string, string>;
  confirmationLoadingIds: Set<string>;
}) {
  // 已结算：展示当时持久化的最后有效判断
  if (settled && savedExecution) {
    const code = savedExecutionDecisionCode(savedExecution);
    return (
      <span style={executionDecisionTagStyle(code)} title={savedExecutionTitle(savedExecution)}>
        {renderSavedExecution(savedExecution)}
      </span>
    );
  }
  // 未结算：展示实时买入确认
  const code = confirmationLoadingIds.has(id) ? "loading" : executionDecisionCode(id, confirmationMap, confirmationErrors);
  return (
    <span style={executionDecisionTagStyle(code)} title={executionDecisionTitle(id, confirmationMap, confirmationErrors)}>
      {renderExecutionDecision(id, confirmationMap, confirmationErrors, confirmationLoadingIds)}
    </span>
  );
}

function ConditionSummaryValue({
  settled,
  savedExecution,
  confirmationMap,
  id,
}: {
  settled: boolean;
  savedExecution: RecommendationExecutionState | null;
  confirmationMap: Record<string, ExecutionConfirmationItem>;
  id: string;
}) {
  const condition = settled
    ? savedExecution?.condition_summary
    : confirmationMap[id]?.condition_summary || savedExecution?.condition_summary;
  if (!condition?.label) return <span className="text-muted">—</span>;
  return (
    <span
      title={executionConditionTitle(condition)}
      style={{ color: condition.passed ? "var(--market-up)" : "var(--muted)" }}
    >
      {condition.passed ? "当前买入条件已满足" : "当前买入判断条件"}: {clip(condition.label, 36)}
    </span>
  );
}

function HoldingDecisionValue({
  id,
  confirmationMap,
  fallback,
}: {
  id: string;
  confirmationMap: Record<string, ExecutionConfirmationItem>;
  fallback?: { code?: string; label?: string; reason?: string } | null;
}) {
  const decision = confirmationMap[id]?.holding_decision || fallback;
  if (!decision) return <span className="text-muted">—</span>;
  const code = String(decision.code || "observe");
  return (
    <span style={executionDecisionTagStyle(code)} title={decision.reason || undefined}>
      {decision.label || holdingDecisionDefaultLabel(code)}
    </span>
  );
}

function buildMockConfirmations(
  ids: string[],
  items: RecommendationHistoryResponse["items"],
): ExecutionConfirmationResponse {
  const now = new Date().toISOString();
  const resItems: ExecutionConfirmationItem[] = [];
  const pnlItems: RealtimePnlItem[] = [];
  for (const id of ids) {
    const it = items.find((x) => x.recommendation_id === id);
    if (!it) continue;
    const hd = (it as DemoHistoryItem).holding_decision;
    const price = it.recommend_price ? it.recommend_price * (1 + (Math.random() - 0.5) * 0.04) : 0;
    resItems.push({
      recommendation_id: id,
      code: it.code,
      trade_date: it.trade_date,
      status: "ok",
      fresh: true,
      current_price: price,
      quote_time: now,
      entry_decision: it.execution_state
        ? {
            code: it.execution_state.latest_decision_code || "wait",
            label: it.execution_state.latest_decision_label || "继续等待",
            reason: it.execution_state.latest_reason || "",
          }
        : { code: "not_ready", label: "等待实时确认", reason: "" },
      holding_decision: hd
        ? {
            code: hd.code || "observe",
            label: hd.label || "持仓判断待确认",
            reason: hd.reason || "",
          }
        : undefined,
      condition_summary: {
        label: "收盘价 ≥ 理想买点",
        passed: it.execution_state?.latest_decision_code === "buy",
        expression: "close >= ideal_buy",
      },
    });
    pnlItems.push({
      recommendation_id: id,
      code: it.code,
      trade_date: it.trade_date,
      recommend_price: it.recommend_price,
      status: "holding",
      entry_price: it.recommend_price,
      entry_date: it.trade_date,
      now_price: price,
      pnl_pct: ((price - it.recommend_price) / it.recommend_price) * 100,
    });
  }
  return {
    computed_at: now,
    cached: false,
    summary: {
      requested_count: ids.length,
      eligible_count: ids.length,
      settled_skipped_count: 0,
      expired_count: 0,
      success_count: ids.length,
      error_count: 0,
    },
    items: resItems,
    errors: [],
    pnl_items: pnlItems,
  };
}

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
  return `最后判断：${state.latest_decision_label || state.latest_decision_code}${price}`;
}

function savedExecutionTitle(state: RecommendationExecutionState) {
  const checkedAt = state.latest_checked_at
    ? ` · 判断于 ${formatApiDateTime(state.latest_checked_at)}`
    : "";
  return `${state.latest_decision_label || state.latest_decision_code}${state.latest_reason ? ` · ${state.latest_reason}` : ""}${checkedAt}`;
}

function executionConditionTitle(condition: { label?: string; expression?: string; observed?: Record<string, unknown> }) {
  const expression = condition.expression ? `规则：${condition.expression}` : "";
  const observed = condition.observed && Object.keys(condition.observed).length > 0
    ? `当前值：${Object.entries(condition.observed).map(([key, value]) => `${key}=${String(value)}`).join("，")}`
    : "";
  return [condition.label || "买点判断条件", expression, observed].filter(Boolean).join(" · ");
}

const clip = (value: string, max: number) => {
  if (value.length <= max) return value;
  return `${value.slice(0, max)}…`;
};

function renderRealtimePnl(item: RealtimePnlItem | undefined) {
  if (!item) return <span className="text-muted">—</span>;
  if (item.status === "holding" && item.pnl_pct != null) {
    const up = item.pnl_pct >= 0;
    return (
      <>
        <span style={{ color: up ? "var(--market-up)" : "var(--market-down)", fontWeight: 600 }} title={`入场 ${item.entry_price} · 现价 ${item.now_price}`}>
          {up ? "+" : ""}{item.pnl_pct.toFixed(2)}%
        </span>
        <span className="hdemo-return-note">未结算</span>
      </>
    );
  }
  if (item.status === "watching") {
    const dist = item.distance_to_target_pct;
    return (
      <>
        <span className="text-muted" style={{ fontSize: "var(--text-sm)" }} title={item.reason || "买点未触发"}>
          距买点 {dist != null ? `${dist >= 0 ? "+" : ""}${dist.toFixed(1)}%` : "—"}
        </span>
        <span className="hdemo-return-note">未结算</span>
      </>
    );
  }
  if (item.status === "waiting") {
    return (
      <>
        <span className="text-muted" style={{ fontSize: "var(--text-sm)" }} title={item.reason}>
          待下一交易日入场
        </span>
        <span className="hdemo-return-note">未结算</span>
      </>
    );
  }
  return <span className="text-muted" title={item.reason || "实时行情不可用"}>—</span>;
}
