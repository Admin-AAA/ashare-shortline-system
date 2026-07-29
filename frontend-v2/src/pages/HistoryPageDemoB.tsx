import {
  ArrowLeft,
  Calendar,
  ChevronDown,
  ChevronRight,
  Filter,
  Layers,
  LayoutList,
  Maximize2,
  PanelLeftClose,
  PanelLeftOpen,
  RotateCcw,
  Search,
  Target,
  TrendingDown,
  TrendingUp,
  Zap,
} from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Panel } from "../components/Panel";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";
import { fmtPrice } from "../components/Shared";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";
import { api } from "../api/client";
import type { RecommendationHistoryResponse } from "../types";
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
import { generateMockHistory, holdingDecisionDefaultLabel, holdingDecisionTone, type DemoHistoryItem } from "./historyDemoData";

/**
 * Demo B —— 专业交易终端表格
 * 适合高频盯盘、需要高信息密度和快速横向对比的用户。
 * 布局：左侧筛选抽屉 + 顶部迷你统计条 + 高密度可展开表格。
 *
 * 新增：实时决策列并列展示 entry_decision（买点）与 holding_decision（卖点/持仓），
 * 买入与卖出使用同一份实时行情。
 */
export function HistoryPageDemoB() {
  const { navigate } = useUrlRoute();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [label, setLabel] = useState<"" | "success" | "failed" | "missed" | "pending">("");
  const [datePreset, setDatePreset] = useState<"7d" | "30d" | "month" | "all">("30d");
  const [codeInput, setCodeInput] = useState("");
  const [codeQuery, setCodeQuery] = useState("");
  const [bucket, setBucket] = useState("");
  const [setupType, setSetupType] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: realData, loading, error } = useApi<RecommendationHistoryResponse>(
    async () => api.recommendationHistory({ limit: 100 }),
    [],
  );
  const data = realData ?? (error ? generateMockHistory(60) : undefined);

  const effectiveRange = useMemo(() => {
    if (datePreset === "all") return { from: "", to: "" };
    const today = new Date();
    const to = today.toISOString().slice(0, 10);
    const from = new Date(today);
    if (datePreset === "7d") from.setDate(from.getDate() - 6);
    else if (datePreset === "30d") from.setDate(from.getDate() - 29);
    else if (datePreset === "month") from.setDate(1);
    return { from: from.toISOString().slice(0, 10), to };
  }, [datePreset]);

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
    setCodeInput("");
    setCodeQuery("");
    setBucket("");
    setSetupType("");
  };

  if (loading) return <PageSkeleton title="复盘" hint="加载推荐历史" rowCount={10} />;
  if (!data) return <ErrorPage title="历史加载失败" message={error || "无数据"} />;

  return (
    <Panel
      kicker="重构 Demo B"
      title="交易终端视图"
      actions={
        <button className="btn btn--ghost" onClick={() => navigate("/history")}>
          <ArrowLeft size={14} /> 返回原复盘页
        </button>
      }
    >
      {/* ── 顶部迷你统计条 ── */}
      <div className="hdemo-terminal-bar">
        <div className="hdemo-terminal-stat">
          <span className="hdemo-terminal-stat__label">胜率</span>
          <span className={`hdemo-terminal-stat__value ${summary.winRate >= 0 ? "hdemo-terminal-stat__value--up" : "hdemo-terminal-stat__value--down"}`}>
            {summary.winRate.toFixed(1)}%
          </span>
          <span className="hdemo-terminal-stat__sub">{summary.winCount}/{summary.tradeCount}</span>
        </div>
        <div className="hdemo-terminal-stat">
          <span className="hdemo-terminal-stat__label">平均净收益</span>
          <span className={`hdemo-terminal-stat__value ${summary.avgReturn >= 0 ? "hdemo-terminal-stat__value--up" : "hdemo-terminal-stat__value--down"}`}>
            {summary.avgReturn >= 0 ? "+" : ""}{summary.avgReturn.toFixed(2)}%
          </span>
        </div>
        <div className="hdemo-terminal-stat">
          <span className="hdemo-terminal-stat__label">累计净收益</span>
          <span className={`hdemo-terminal-stat__value ${summary.totalReturn >= 0 ? "hdemo-terminal-stat__value--up" : "hdemo-terminal-stat__value--down"}`}>
            {summary.totalReturn >= 0 ? "+" : ""}{summary.totalReturn.toFixed(2)}%
          </span>
        </div>
        <div className="hdemo-terminal-stat">
          <span className="hdemo-terminal-stat__label">匹配笔数</span>
          <span className="hdemo-terminal-stat__value">{filteredItems.length}</span>
        </div>
      </div>

      <div className="hdemo-terminal-layout">
        {/* ── 左侧筛选抽屉 ── */}
        <aside className={`hdemo-terminal-sidebar${sidebarOpen ? "" : " hdemo-terminal-sidebar--closed"}`}>
          <div className="hdemo-terminal-sidebar__header">
            <Filter size={14} />
            <span>筛选器</span>
            <button className="hdemo-terminal-sidebar__toggle" onClick={() => setSidebarOpen(false)} title="收起">
              <PanelLeftClose size={14} />
            </button>
          </div>

          <div className="hdemo-terminal-sidebar__body">
            <FilterGroup title="时间范围" icon={<Calendar size={12} />}>
              <ChipGroup
                options={[
                  { key: "7d", label: "7天" },
                  { key: "30d", label: "30天" },
                  { key: "month", label: "本月" },
                  { key: "all", label: "全部" },
                ]}
                value={datePreset}
                onChange={(v) => setDatePreset(v as typeof datePreset)}
              />
            </FilterGroup>

            <FilterGroup title="结果" icon={<Zap size={12} />}>
              <select className="cfg-input" value={label} onChange={(e) => setLabel(e.target.value as typeof label)}>
                <option value="">全部</option>
                <option value="success">成功</option>
                <option value="failed">失败</option>
                <option value="missed">未触发</option>
                <option value="pending">待标注</option>
              </select>
            </FilterGroup>

            <FilterGroup title="股票代码" icon={<Search size={12} />}>
              <input
                className="cfg-input"
                placeholder="6 位代码"
                value={codeInput}
                onChange={(e) => setCodeInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && setCodeQuery(codeInput.trim())}
              />
            </FilterGroup>

            <FilterGroup title="策略桶" icon={<Layers size={12} />}>
              <select className="cfg-input" value={bucket} onChange={(e) => setBucket(e.target.value)}>
                <option value="">全部</option>
                {Object.entries(BUCKET_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </FilterGroup>

            <FilterGroup title="买点" icon={<Target size={12} />}>
              <select className="cfg-input" value={setupType} onChange={(e) => setSetupType(e.target.value)}>
                <option value="">全部</option>
                {setupOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </FilterGroup>

            <div className="hdemo-terminal-sidebar__actions">
              <button className="btn btn--primary" style={{ flex: 1 }} onClick={() => setCodeQuery(codeInput.trim())}>
                <Search size={13} /> 查询
              </button>
              <button className="btn btn--ghost" onClick={resetFilters}>
                <RotateCcw size={13} />
              </button>
            </div>
          </div>
        </aside>

        {/* ── 主表格区 ── */}
        <div className="hdemo-terminal-main">
          {!sidebarOpen && (
            <button className="hdemo-terminal-sidebar__open" onClick={() => setSidebarOpen(true)} title="展开筛选">
              <PanelLeftOpen size={14} />
            </button>
          )}

          {filteredItems.length === 0 ? (
            <EmptyState icon={<LayoutList size={32} />} title="无匹配推荐" reason="调整左侧筛选条件后重试" />
          ) : (
            <div className="hdemo-terminal-table-wrap">
              <table className="hdemo-terminal-table">
                <thead>
                  <tr>
                    <th className="hdemo-terminal-th hdemo-terminal-th--fixed" style={{ width: 32 }} />
                    <th className="hdemo-terminal-th">日期</th>
                    <th className="hdemo-terminal-th">代码 / 名称</th>
                    <th className="hdemo-terminal-th" style={{ textAlign: "right" }}>评分</th>
                    <th className="hdemo-terminal-th">策略桶</th>
                    <th className="hdemo-terminal-th">买点</th>
                    <th className="hdemo-terminal-th" style={{ textAlign: "right" }}>推荐价</th>
                    <th className="hdemo-terminal-th" style={{ textAlign: "right" }}>止损</th>
                    <th className="hdemo-terminal-th" style={{ textAlign: "right" }}>止盈</th>
                    <th className="hdemo-terminal-th">状态</th>
                    <th className="hdemo-terminal-th" style={{ minWidth: 140 }}>实时决策</th>
                    <th className="hdemo-terminal-th" style={{ textAlign: "right", minWidth: 120 }}>收益</th>
                    <th className="hdemo-terminal-th">退出 / 持仓</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((it) => {
                    const o = it.outcome;
                    const settled = isOutcomeSettled(o?.outcome_label);
                    const tone = outcomeTone(o?.outcome_label);
                    const ret = settled ? o?.executed_return : undefined;
                    const up = ret != null && ret >= 0;
                    const expanded = expandedId === it.recommendation_id;
                    const currentPrice = it.recommend_price ? it.recommend_price * (1 + (Math.random() - 0.5) * 0.06) : undefined;
                    return (
                      <Fragment key={it.recommendation_id}>
                        <tr
                          className={`hdemo-terminal-tr${expanded ? " hdemo-terminal-tr--expanded" : ""}`}
                          onClick={() => setExpandedId(expanded ? null : it.recommendation_id)}
                        >
                          <td className="hdemo-terminal-td hdemo-terminal-td--fixed">
                            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          </td>
                          <td className="hdemo-terminal-td">
                            <div className="hdemo-terminal-stack">
                              <span>{it.trade_date}</span>
                              {it.recommended_at && (
                                <span className="hdemo-terminal-muted">
                                  {formatApiDateTime(it.recommended_at, { hour: "2-digit", minute: "2-digit" })}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="hdemo-terminal-td">
                            <div className="hdemo-terminal-stack">
                              <span className="hdemo-terminal-code">{it.code}</span>
                              <span className="hdemo-terminal-name">{it.name}</span>
                            </div>
                          </td>
                          <td className="hdemo-terminal-td" style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                            {it.final_score.toFixed(1)}
                          </td>
                          <td className="hdemo-terminal-td">
                            <span className="hdemo-terminal-badge hdemo-terminal-badge--bucket">{bucketLabel(it.bucket)}</span>
                          </td>
                          <td className="hdemo-terminal-td">
                            <span className="hdemo-terminal-muted">{setupLabel(it.setup_type).label}</span>
                          </td>
                          <td className="hdemo-terminal-td" style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                            {fmtPrice(it.recommend_price)}
                          </td>
                          <td className="hdemo-terminal-td" style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                            {fmtPrice(it.stop_loss)}
                          </td>
                          <td className="hdemo-terminal-td" style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                            {fmtPrice(it.take_profit)}
                          </td>
                          <td className="hdemo-terminal-td">
                            <span className={`hdemo-terminal-status hdemo-terminal-status--${tone}`}>
                              <span className="hdemo-terminal-status__dot" />
                              {outcomeLabel(o?.outcome_label)}
                            </span>
                          </td>
                          <td className="hdemo-terminal-td">
                            <div className="hdemo-terminal-stack" style={{ gap: 4 }}>
                              <DecisionLine
                                type="entry"
                                code={it.execution_state?.latest_decision_code}
                                label={it.execution_state?.latest_decision_label}
                                fallback="未买入"
                              />
                              <DecisionLine
                                type="holding"
                                code={getHoldingDecision(it)?.code}
                                label={getHoldingDecision(it)?.label}
                                reason={getHoldingDecision(it)?.reason}
                                currentPrice={currentPrice}
                                fallback="未持仓"
                              />
                            </div>
                          </td>
                          <td className="hdemo-terminal-td" style={{ textAlign: "right" }}>
                            {ret != null ? (
                              <div className="hdemo-terminal-return">
                                <span className={`hdemo-terminal-return__value ${up ? "hdemo-terminal-return__value--up" : "hdemo-terminal-return__value--down"}`}>
                                  {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                                  {`${up ? "+" : ""}${ret.toFixed(2)}%`}
                                </span>
                                <div className="hdemo-terminal-return__bar">
                                  <div
                                    className={`hdemo-terminal-return__fill ${up ? "hdemo-terminal-return__fill--up" : "hdemo-terminal-return__fill--down"}`}
                                    style={{ width: `${Math.min(Math.abs(ret) * 3, 100)}%` }}
                                  />
                                </div>
                              </div>
                            ) : (
                              <span className="hdemo-terminal-muted">—</span>
                            )}
                          </td>
                          <td className="hdemo-terminal-td">
                            <div className="hdemo-terminal-stack">
                              <span>{exitReasonLabel(o?.exit_reason, "—", ret)}</span>
                              {o?.holding_days != null && <span className="hdemo-terminal-muted">{o.holding_days} 天</span>}
                            </div>
                          </td>
                        </tr>
                        {expanded && (
                          <tr className="hdemo-terminal-detail-row">
                            <td colSpan={12} className="hdemo-terminal-detail-cell">
                              <div className="hdemo-terminal-detail">
                                <div className="hdemo-terminal-detail__grid">
                                  <DetailCol title="入场计划">
                                    <Row label="理想买" value={fmtPrice(it.ideal_buy)} />
                                    <Row label="次买" value={fmtPrice(it.secondary_buy)} />
                                    <Row label="规则" value={it.entry_rule || "—"} />
                                  </DetailCol>
                                  <DetailCol title="买入与卖出判断">
                                    <Row label="买入判断" value={it.execution_state?.latest_decision_label || "—"} />
                                    <Row
                                      label="持仓/卖出判断"
                                      value={getHoldingDecision(it) ? `${getHoldingDecision(it)!.label} · ${getHoldingDecision(it)!.reason}` : "—"}
                                    />
                                    <Row label="判断时间" value={it.execution_state?.latest_checked_at ? formatApiDateTime(it.execution_state.latest_checked_at, { hour: "2-digit", minute: "2-digit" }) : "—"} />
                                  </DetailCol>
                                  <DetailCol title="附加信息">
                                    <Row label="行业" value={it.industry || "—"} />
                                    <Row label="LLM 态度" value={it.llm_stance || "—"} />
                                    <Row label="风险等级" value={it.risk_level || "—"} />
                                  </DetailCol>
                                </div>
                                <div className="hdemo-terminal-detail__actions">
                                  <button className="btn btn--ghost" onClick={(e) => e.stopPropagation()}>
                                    <Maximize2 size={13} /> 完整详情
                                  </button>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Panel>
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
    <div className="hdemo-terminal-chipgroup">
      {options.map((opt) => (
        <button
          key={opt.key}
          className={`hdemo-terminal-chip${value === opt.key ? " hdemo-terminal-chip--active" : ""}`}
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

function FilterGroup({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="hdemo-filter-group">
      <div className="hdemo-filter-group__title">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function DetailCol({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="hdemo-terminal-detail__col">
      <div className="hdemo-terminal-detail__col-title">{title}</div>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="hdemo-terminal-detail__row">
      <span className="hdemo-terminal-detail__row-label">{label}</span>
      <span className="hdemo-terminal-detail__row-value">{value}</span>
    </div>
  );
}
function getHoldingDecision(item: RecommendationHistoryResponse["items"][number]) {
  return (item as DemoHistoryItem).holding_decision;
}

function DecisionLine({
  type,
  code,
  label,
  reason,
  currentPrice,
  fallback,
}: {
  type: "entry" | "holding";
  code?: string | null;
  label?: string | null;
  reason?: string | null;
  currentPrice?: number;
  fallback: string;
}) {
  const hasDecision = Boolean(code);
  const tone = type === "entry"
    ? (code === "buy" ? "positive" : code === "wait" ? "warning" : code === "cancel" ? "danger" : "neutral")
    : holdingDecisionTone(code);
  const displayLabel = label || (hasDecision ? holdingDecisionDefaultLabel(code) : fallback);
  const prefix = type === "entry" ? "买" : "持";
  return (
    <div className={`hdemo-terminal-decision hdemo-terminal-decision--${tone}`} title={reason || undefined}>
      <span className="hdemo-terminal-decision__prefix">{prefix}</span>
      <span className="hdemo-terminal-decision__label">{displayLabel}</span>
      {type === "holding" && currentPrice != null && (
        <span className="hdemo-terminal-decision__price">¥{currentPrice.toFixed(2)}</span>
      )}
    </div>
  );
}
