import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import BacktestPanel from "../../components/BacktestPanel";
import BacktestEvaluationPanel from "./components/BacktestEvaluationPanel";
import DataHealthDetailPanel from "./components/DataHealthDetailPanel";
import DataHealthPanel from "./components/DataHealthPanel";
import DragonCandidatesPanel from "./components/DragonCandidatesPanel";
import EvaluationBreakdownPanel from "./components/EvaluationBreakdownPanel";
import MarketTemperaturePanel from "./components/MarketTemperaturePanel";
import MetricsPanel from "./components/MetricsPanel";
import QualityScorecardPanel from "./components/QualityScorecardPanel";
import RiskRadarPanel from "./components/RiskRadarPanel";
import RunStatusPanel from "./components/RunStatusPanel";
import TemporalConsistencyPanel from "./components/TemporalConsistencyPanel";
import ValidationPanel from "./components/ValidationPanel";
import { useDashboardViewModel } from "./hooks/useDashboardViewModel";
import { api } from "../../api/client";
import type { CockpitSection } from "../../app/nav";
import {
  aShareDirectionClass,
  businessStatusLabel,
  candidateEmptyState,
  outcomeStatusLabel,
  readinessCopy,
  recommendationStatusLabel,
  riskLevelLabel,
  runStatusLabel,
  runStatusCopy,
  watchStatusLabel,
} from "../../utils/display";
import type {
  CockpitRecommendation,
  DashboardOverview,
  MetricsOverview,
  RecommendationDetail,
  ReplayAnalysisResult,
  ReplayAnalysisRow,
  DashboardDataHealthExample,
} from "../../types";

type DashboardSection = Exclude<CockpitSection, "control">;

export default function DashboardPage({ section, refreshSignal = 0 }: { section: DashboardSection; refreshSignal?: number }) {
  const { data, loading, error, refresh } = useDashboardViewModel(refreshSignal);
  const [metrics, setMetrics] = useState<MetricsOverview | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricsError, setMetricsError] = useState("");
  const [slowLoading, setSlowLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setMetricsLoading(true);
    setMetricsError("");
    api.metricsOverview("formal", { signal: controller.signal }).then((m) => {
      if (!controller.signal.aborted) setMetrics(m);
    }).catch((err) => {
      if (!controller.signal.aborted && !isAbortError(err)) setMetricsError(err.message);
    }).finally(() => {
      if (!controller.signal.aborted) setMetricsLoading(false);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!loading) {
      setSlowLoading(false);
      return;
    }
    const timer = window.setTimeout(() => setSlowLoading(true), 2500);
    return () => window.clearTimeout(timer);
  }, [loading]);

  if (loading) return <DashboardLoadingState slow={slowLoading} />;
  if (error) {
    return (
      <div className="cockpit-panel error-state">
        <strong>驾驶舱加载失败</strong>
        <span>{error}</span>
        <div className="state-actions">
          <button type="button" onClick={() => void refresh()}>重试</button>
          <button type="button" onClick={goToControlCenter}>去控制中心</button>
        </div>
      </div>
    );
  }
  if (!data) return <DashboardEmptyState onRetry={() => void refresh()} />;

  if (section === "run") return <RunWorkspace data={data} />;
  if (section === "pool") return <CandidateWorkspace data={data} />;
  if (section === "data") return <DataWorkspace data={data} />;
  if (section === "history") return <ReviewWorkspace data={data} />;
  if (section === "validation") return <ValidationWorkspace data={data} />;

  return (
    <div className="dashboard-grid">
      <div className="dashboard-column">
        <MarketTemperaturePanel market={data.market} />
        <QualityScorecardPanel scorecard={data.quality_scorecard} />
        <RiskRadarPanel radar={data.risk_radar} />
      </div>
      <div className="dashboard-column">
        <MetricsPanel metrics={metrics ?? ({} as MetricsOverview)} loading={metricsLoading} />
        <RunStatusPanel run={data.current_run} />
      </div>
      <div className="dashboard-column">
        <DragonCandidatesPanel items={data.recommendations} emptyState={candidateEmptyState(data)} />
        <DataHealthPanel dataHealth={data.data_health} taskHealth={data.task_health} />
        <ValidationPanel validation={data.validation} />
      </div>
    </div>
  );
}

function DashboardLoadingState({ slow }: { slow: boolean }) {
  return (
    <div className="cockpit-panel loading-state enhanced">
      <div className="loading-title">
        <Loader2 className="spin" size={18} />
        <strong>正在加载驾驶舱数据</strong>
      </div>
      <p>{slow ? "后端正在整理行情、评估和数据池状态，当前请求还在继续。" : "正在连接后端并读取市场温度、候选解释和数据健康。"}</p>
      <div className="loading-skeleton-grid" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <small>{slow ? "如果长时间停留在这里，可以点击顶部刷新或进入控制中心查看数据池状态。" : "通常几秒内完成，慢查询会在页面上持续显示状态。"}</small>
    </div>
  );
}

function DashboardEmptyState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="cockpit-panel empty-state enhanced">
      <strong>暂无驾驶舱数据</strong>
      <span>页面已正常渲染，但后端没有返回可展示的驾驶舱快照。</span>
      <div className="state-actions">
        <button type="button" onClick={onRetry}>重新加载</button>
        <button type="button" onClick={goToControlCenter}>去控制中心检查</button>
      </div>
    </div>
  );
}

function isAbortError(err: unknown) {
  return err instanceof Error && err.name === "AbortError";
}

function goToControlCenter() {
  window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "control" } }));
}

function RunWorkspace({ data }: { data: DashboardOverview }) {
  const runCopy = runStatusCopy(data.current_run);
  const stages = [
    { key: "prepare", label: "数据池准备", note: "本地池、行情源和交易日对齐", phases: ["prepare", "starting", "market_data", "auxiliary_data"] },
    { key: "candidate", label: "候选过滤", note: "流动性、主线、风险门槛", phases: ["candidate", "scope_filter", "feature_build", "rule_scan"] },
    { key: "skill_review", label: "Skill 审查", note: "规则分 + Agent 解释", phases: ["skill_gate", "skill_review"] },
    { key: "persist", label: "聚合落库", note: "分桶、排序、复盘闭环", phases: ["aggregate", "aggregate_metrics", "persist", "completed"] },
  ];
  const stageStates = resolveRunStageStates(data.current_run.status, data.current_run.phase, stages.map((stage) => stage.phases));
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>推荐运行</span>
          <h2>运行编排</h2>
          <p>围绕当前 run 的阶段、心跳、可恢复性和候选产出建立运行视角。</p>
        </div>
        <strong>{runCopy.status}</strong>
      </section>
      <div className="focus-grid">
        <RunStatusPanel run={data.current_run} />
        <section className="cockpit-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Pipeline</div>
              <h2>阶段轨迹</h2>
            </div>
          </div>
          <div className="timeline-list">
            {stages.map((stage, index) => (
              <span key={stage.key} className={stageStates[index].className}>
                <strong>{stage.label}</strong>
                <small>{stage.note}</small>
                <em>{stageStates[index].label}</em>
              </span>
            ))}
          </div>
        </section>
      </div>
      <DragonCandidatesPanel items={data.recommendations.slice(0, 6)} emptyState={candidateEmptyState(data)} />
    </div>
  );
}

function resolveRunStageStates(status: unknown, phase: unknown, stagePhases: string[][]) {
  const statusKey = String(status ?? "").toLowerCase();
  const phaseKey = String(phase ?? "").toLowerCase();
  const completed = statusKey === "completed" || phaseKey === "completed";
  const failed = statusKey === "failed" || statusKey === "error" || statusKey === "interrupted";
  const activeIndex = stagePhases.findIndex((phases) => phases.includes(phaseKey));

  return stagePhases.map((_, index) => {
    if (completed) {
      return { className: `done ${index === stagePhases.length - 1 ? "active" : ""}`.trim(), label: "已完成" };
    }
    if (activeIndex === -1) {
      return { className: "", label: index === 0 ? "等待状态同步" : "待执行" };
    }
    if (index < activeIndex) return { className: "done", label: "已完成" };
    if (index === activeIndex) return { className: failed ? "active danger" : "active", label: failed ? "中断于此" : "当前阶段" };
    return { className: "", label: "待执行" };
  });
}

function CandidateWorkspace({ data }: { data: DashboardOverview }) {
  const empty = candidateEmptyState(data);
  const openReplay = (recommendationId: string) => {
    window.sessionStorage.setItem("ashare:pendingReplayId", recommendationId);
    window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "history", recommendationId } }));
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent("ashare:select-replay", { detail: { recommendationId } }));
    }, 120);
  };
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>候选解释</span>
          <h2>个股解释卡</h2>
          <p>每个候选都带强度、原因、风险、共识与数据质量，优先服务短线决策。</p>
        </div>
        <strong>{data.recommendations.length} 只</strong>
      </section>
      <DragonCandidatesPanel items={data.recommendations} emptyState={empty} />
      <div className={`explain-grid ${data.recommendations.length <= 1 ? "single" : ""}`}>
        {data.recommendations.map((item) => (
          <ExplanationCard key={item.recommendation_id} item={item} onOpenReplay={openReplay} />
        ))}
      </div>
    </div>
  );
}

function DataWorkspace({ data }: { data: DashboardOverview }) {
  const messages = stringList(data.data_health.messages);
  const examples = stringList(data.data_health.examples);
  const readiness = readinessCopy(data.data_health.can_run_local_recommendation, data.data_health.status);
  const checkedCandidates = Number(data.data_health.checked_candidate_count ?? 0);
  const readyCandidates = Number(data.data_health.ready_candidate_count ?? 0);
  const exampleLimit = Math.max(1, Number(data.data_health.example_limit ?? 8));
  const exampleTotal = Number(data.data_health.example_count ?? examples.length);
  const visibleExampleCount = Math.min(examples.length, exampleLimit);
  const scopeNote = data.data_health.scope_note
    || "概览只展示缺口样例；刷新本地数据池会全量检查候选池并补齐可处理日K缺口。";
  const exampleNote = `当前展示 ${visibleExampleCount}/${exampleTotal || visibleExampleCount} 条缺口样例；样例不是补齐上限。`;
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>数据健康</span>
          <h2>数据池与调度健康度</h2>
          <p>拆开本地池覆盖、候选可运行性、调度状态和缺口样例，避免运行前盲扫。</p>
        </div>
        <strong>{businessStatusLabel(data.data_health.status)}</strong>
      </section>
      <div className="data-workspace-layout">
        <div className="data-main-stack">
          <div className="focus-grid data-summary-grid">
            <DataHealthPanel dataHealth={data.data_health} taskHealth={data.task_health} />
            <section className="cockpit-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-kicker">Coverage</div>
                  <h2>池覆盖细节</h2>
                </div>
              </div>
              <div className="metric-grid">
                <span>活跃报价<strong>{textValue(data.data_health.active_quote_count)}</strong></span>
                <span>日K覆盖<strong>{textValue(data.data_health.bar_coverage_pct)}%</strong></span>
                <span>合格日K<strong>{textValue(data.data_health.eligible_bar_coverage_pct)}%</strong></span>
                <span>本地推荐<strong>{readiness.value}</strong></span>
                <span>候选检查<strong>{checkedCandidates}</strong></span>
                <span>覆盖完整<strong>{readyCandidates}</strong></span>
                <span>样例展示<strong>{visibleExampleCount}/{exampleTotal || visibleExampleCount}</strong></span>
              </div>
              <p>{readiness.note}</p>
              <div className="data-scope-note">
                <span>{scopeNote}</span>
                <span>{exampleNote}</span>
              </div>
            </section>
          </div>
          <DataHealthDetailPanel />
        </div>
        <aside className="data-side-stack">
          <TemporalConsistencyPanel />
          <StatusStream title="数据池消息" items={messages} empty="暂无数据池消息" />
          <StatusStream title="缺口样例" items={examples} empty="暂无缺口样例" note={exampleNote} />
        </aside>
      </div>
    </div>
  );
}

function ReviewWorkspace({ data }: { data: DashboardOverview }) {
  const [selectedId, setSelectedId] = useState(data.recommendations[0]?.recommendation_id ?? "");
  const [detail, setDetail] = useState<RecommendationDetail | null>(null);
  const [analysis, setAnalysis] = useState<ReplayAnalysisResult | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [reviewMessage, setReviewMessage] = useState("");
  const [filterFalsePositive, setFilterFalsePositive] = useState(false);
  const [reasonFilter, setReasonFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bridgeReplayIdRef = useRef<string | null>(null);

  useEffect(() => {
    const nextId = data.recommendations[0]?.recommendation_id ?? "";
    setSelectedId((current) => current || nextId);
  }, [data.recommendations]);

  useEffect(() => {
    const pendingId = window.sessionStorage.getItem("ashare:pendingReplayId");
    if (!pendingId) return;
    const item = data.recommendations.find((candidate) => candidate.recommendation_id === pendingId);
    if (!item) return;
    window.sessionStorage.removeItem("ashare:pendingReplayId");
    bridgeReplayIdRef.current = pendingId;
    setSelectedId(pendingId);
    setReviewMessage(`已从候选解释进入复盘：${item.name} · ${item.code}`);
  }, [data.recommendations]);

  useEffect(() => {
    function handleSelectFromPool(event: Event) {
      const recommendationId = (event as CustomEvent<{ recommendationId?: string }>).detail?.recommendationId;
      if (recommendationId && data.recommendations.some((item) => item.recommendation_id === recommendationId)) {
        bridgeReplayIdRef.current = recommendationId;
        setSelectedId(recommendationId);
        const item = data.recommendations.find((candidate) => candidate.recommendation_id === recommendationId);
        setReviewMessage(item ? `已从候选解释进入复盘：${item.name} · ${item.code}` : "已从候选解释进入复盘");
      }
    }
    window.addEventListener("ashare:select-replay", handleSelectFromPool);
    return () => window.removeEventListener("ashare:select-replay", handleSelectFromPool);
  }, [data.recommendations]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    api.recommendationDetail(selectedId)
      .then((payload) => {
        if (!cancelled) {
          setDetail(payload);
          if (bridgeReplayIdRef.current === payload.recommendation_id) {
            setReviewMessage(`已从候选解释进入复盘并加载详情：${payload.name} · ${payload.code}`);
          } else {
            setReviewMessage(`复盘详情已加载：${payload.name} · ${payload.code}`);
          }
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setDetail(null);
          setError(err instanceof Error ? err.message : "推荐详情加载失败");
          setReviewMessage("");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  useEffect(() => {
    const runId = detail?.run?.run_id;
    if (!runId) {
      setAnalysis(null);
      return;
    }
    let cancelled = false;
    setAnalysisLoading(true);
    setAnalysisError("");
    api.replayAnalysis(runId)
      .then((payload) => {
        if (!cancelled) {
          setAnalysis(payload);
          setReviewMessage((current) => {
            if (current.startsWith("已从候选解释进入复盘")) return current;
            return `复盘分析已加载：${payload.summary.total_count} 条样本，误判率 ${(payload.summary.false_positive_rate * 100).toFixed(1)}%`;
          });
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setAnalysis(null);
          setAnalysisError(err instanceof Error ? err.message : "复盘分析加载失败");
          setReviewMessage("");
        }
      })
      .finally(() => {
        if (!cancelled) setAnalysisLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [detail?.run?.run_id]);

  const selectedRecommendation = data.recommendations.find((item) => item.recommendation_id === selectedId) ?? data.recommendations[0] ?? null;

  const handleSelectRecommendation = (item: CockpitRecommendation) => {
    bridgeReplayIdRef.current = null;
    setSelectedId(item.recommendation_id);
    setReviewMessage(`已切换复盘样本：${item.name} · ${item.code}`);
  };

  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>复盘结果</span>
          <h2>结果反馈闭环</h2>
          <p>从当前推荐出发，聚焦观察状态、复盘入口和后续结果标注，保持策略可学习。</p>
        </div>
        <strong>{data.market.trade_date || "-"}</strong>
      </section>
      {reviewMessage ? (
        <div className="review-feedback" role="status" aria-live="polite">
          {reviewMessage}
        </div>
      ) : null}
      <div className="review-workspace-layout">
        <aside className="review-side-stack">
          <ReviewQueueSummary items={data.recommendations} selected={selectedRecommendation} />
        </aside>
        <ReviewDetailPanel
          detail={detail}
          loading={loading}
          error={error}
          analysis={analysis}
          analysisLoading={analysisLoading}
          analysisError={analysisError}
          filterFalsePositive={filterFalsePositive}
          reasonFilter={reasonFilter}
          categoryFilter={categoryFilter}
          onToggleFalsePositive={() => setFilterFalsePositive((value) => !value)}
          onReasonFilterChange={setReasonFilter}
          onCategoryFilterChange={setCategoryFilter}
          onFeedback={setReviewMessage}
        />
        <aside className="review-side-stack">
          <div className="review-list">
            {data.recommendations.length ? data.recommendations.map((item) => (
              <button
                key={item.recommendation_id}
                type="button"
                className={`cockpit-panel review-row ${item.recommendation_id === selectedId ? "active" : ""}`}
                onClick={() => handleSelectRecommendation(item)}
              >
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.code} · {item.setup_type || "未识别买点"}</span>
                </div>
                <div>
                  <span>观察状态</span>
                  <strong>{watchStatusLabel(item.watch_status)}</strong>
                </div>
                <div>
                  <span>推荐状态</span>
                  <strong>{recommendationStatusLabel(item.status)}</strong>
                </div>
                <div>
                  <span>强度</span>
                  <strong>{item.final_score.toFixed(1)}</strong>
                </div>
              </button>
            )) : <div className="cockpit-panel empty-state">暂无可复盘推荐</div>}
          </div>
        </aside>
      </div>
    </div>
  );
}

function ReviewQueueSummary({
  items,
  selected,
}: {
  items: CockpitRecommendation[];
  selected: CockpitRecommendation | null;
}) {
  const total = items.length;
  const pendingCount = items.filter((item) => String(item.status ?? "").toLowerCase() === "pending").length;
  const watchedCount = items.filter((item) => {
    const status = String(item.watch_status ?? "").toLowerCase();
    return status && status !== "none";
  }).length;
  const scores = items.map((item) => item.final_score).filter((value) => Number.isFinite(value));
  const averageScore = scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null;
  const maxScore = scores.length ? Math.max(...scores) : null;
  const minScore = scores.length ? Math.min(...scores) : null;
  const nextAction = selected
    ? selected.watch_status && selected.watch_status !== "none"
      ? "继续补充 outcome、退出原因和人工复盘"
      : "先标记观察状态，再补充后续 outcome"
    : "等待下一次推荐运行产出样本";

  return (
    <section className="cockpit-panel review-summary-card">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Queue</div>
          <h2>复盘队列概览</h2>
        </div>
      </div>
      <div className="review-summary-grid">
        <span>样本<strong>{total}</strong></span>
        <span>待处理<strong>{pendingCount}</strong></span>
        <span>已观察<strong>{watchedCount}</strong></span>
        <span>均分<strong>{averageScore == null ? "-" : averageScore.toFixed(1)}</strong></span>
      </div>
      <div className="review-summary-note">
        <strong>{selected ? `${selected.name} · ${selected.code}` : "暂无选中样本"}</strong>
        <span>{selected ? `${recommendationStatusLabel(selected.status)} · ${watchStatusLabel(selected.watch_status)} · 强度 ${selected.final_score.toFixed(1)}` : "暂无可复盘推荐"}</span>
      </div>
      <div className="review-summary-note">
        <strong>强度区间</strong>
        <span>{maxScore == null || minScore == null ? "-" : `${minScore.toFixed(1)} - ${maxScore.toFixed(1)}`}</span>
      </div>
      <div className="review-summary-next">{nextAction}</div>
    </section>
  );
}

function ReviewDetailPanel({
  detail,
  loading,
  error,
  analysis,
  analysisLoading,
  analysisError,
  filterFalsePositive,
  reasonFilter,
  categoryFilter,
  onToggleFalsePositive,
  onReasonFilterChange,
  onCategoryFilterChange,
  onFeedback,
}: {
  detail: RecommendationDetail | null;
  loading: boolean;
  error: string;
  analysis: ReplayAnalysisResult | null;
  analysisLoading: boolean;
  analysisError: string;
  filterFalsePositive: boolean;
  reasonFilter: string;
  categoryFilter: string;
  onToggleFalsePositive: () => void;
  onReasonFilterChange: (value: string) => void;
  onCategoryFilterChange: (value: string) => void;
  onFeedback: (value: string) => void;
}) {
  if (loading) {
    return <section className="cockpit-panel loading-state">正在加载推荐复盘详情</section>;
  }
  if (error) {
    return (
      <section className="cockpit-panel error-state">
        <strong>推荐详情加载失败</strong>
        <span>{error}</span>
      </section>
    );
  }
  if (!detail) {
    return <section className="cockpit-panel empty-state">选择一条推荐查看复盘详情</section>;
  }

  const replay = detail.replay_summary ?? {};
  const signalFeatures = replay.signal_features as Record<string, unknown> | undefined;
  const allRows = analysis?.rows ?? [];
  const filteredRows = allRows.filter((row) => {
    if (filterFalsePositive && !row.is_false_positive) return false;
    if (reasonFilter !== "all" && (row.false_positive_reason ?? "未归因") !== reasonFilter) return false;
    if (categoryFilter !== "all" && (row.false_positive_category ?? "unknown") !== categoryFilter) return false;
    return true;
  });
  const reasonOptions = ["all", ...new Set((analysis?.summary.reason_counts ?? []).map((item) => item.reason))];
  const categoryOptions = ["all", ...new Set((analysis?.summary.category_counts ?? []).map((item) => item.category))];
  const categoryLabels = new Map((analysis?.summary.category_counts ?? []).map((item) => [item.category, item.category_label]));
  const buildFilterMessage = (nextFalsePositive = filterFalsePositive, nextReason = reasonFilter, nextCategory = categoryFilter) => {
    const nextRows = allRows.filter((row) => {
      if (nextFalsePositive && !row.is_false_positive) return false;
      if (nextReason !== "all" && (row.false_positive_reason ?? "未归因") !== nextReason) return false;
      if (nextCategory !== "all" && (row.false_positive_category ?? "unknown") !== nextCategory) return false;
      return true;
    });
    const activeFilters = [
      nextFalsePositive ? "仅看误判" : "",
      nextReason !== "all" ? `原因：${nextReason}` : "",
      nextCategory !== "all" ? `类别：${categoryLabels.get(nextCategory) ?? nextCategory}` : "",
    ].filter(Boolean);
    return `当前筛选 ${nextRows.length}/${allRows.length} 条${activeFilters.length ? ` · ${activeFilters.join(" · ")}` : " · 全部样本"}`;
  };
  const handleToggleFalsePositive = () => {
    const nextValue = !filterFalsePositive;
    onToggleFalsePositive();
    onFeedback(buildFilterMessage(nextValue, reasonFilter, categoryFilter));
  };
  const handleReasonFilterChange = (value: string) => {
    onReasonFilterChange(value);
    onFeedback(buildFilterMessage(filterFalsePositive, value, categoryFilter));
  };
  const handleCategoryFilterChange = (value: string) => {
    onCategoryFilterChange(value);
    onFeedback(buildFilterMessage(filterFalsePositive, reasonFilter, value));
  };
  const handleExportRows = () => {
    if (!filteredRows.length) {
      onFeedback("当前筛选为空，未生成导出文件");
      return;
    }
    exportReplayRows(filteredRows);
    onFeedback(`已导出当前筛选：${filteredRows.length} 条`);
  };

  return (
    <section className="cockpit-panel review-detail-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Replay</div>
          <h2>{detail.name} 复盘摘要</h2>
        </div>
      </div>
      <div className="metric-grid" style={{ marginTop: 12 }}>
        <span>推荐时间<strong>{textValue(replay.recommend_time, detail.trade_date)}</strong></span>
        <span>入场<strong>{textValue(replay.entry_time, "-")}</strong></span>
        <span>出场<strong>{textValue(replay.exit_time, "-")}</strong></span>
        <span>净收益<strong className={aShareDirectionClass(replay.net_return_pct)}>
          {replay.net_return_pct == null ? "-" : `${Number(replay.net_return_pct).toFixed(2)}%`}
        </strong></span>
        <span>最终分<strong>{replay.final_score == null ? "-" : Number(replay.final_score).toFixed(1)}</strong></span>
        <span>有效分数线<strong>{replay.effective_score_line == null ? "-" : Number(replay.effective_score_line).toFixed(1)}</strong></span>
      </div>
      <div className="sub-metrics" style={{ marginTop: 12 }}>
        <div className="sub-metrics-group">
          <span className="group-label">触发条件</span>
          <div className="sub-metrics-row">
            {replay.trigger_conditions?.length
              ? replay.trigger_conditions.map((item) => <span key={item}>{item}</span>)
              : <span>暂无</span>}
          </div>
        </div>
        <div className="sub-metrics-group">
          <span className="group-label">误判/退出</span>
          <div className="sub-metrics-row">
            <span>Outcome <strong>{outcomeStatusLabel(replay.outcome_label, "-")}</strong></span>
            <span>退出原因 <strong>{textValue(replay.exit_reason, "-")}</strong></span>
            <span>归因类别 <strong>{textValue(replay.false_positive_category_label, "-")}</strong></span>
            <span>误判原因 <strong>{textValue(replay.false_positive_reason, "-")}</strong></span>
          </div>
        </div>
      </div>
      <div className="fp-list" style={{ marginTop: 12 }}>
        <div className="fp-item">
          <strong>Outcome 说明</strong>
          <span>{textValue(replay.outcome_reason, "暂无")}</span>
        </div>
        <div className="fp-item">
          <strong>人工复盘</strong>
          <span>{textValue(replay.review_failure_reason, detail.review_note?.review_text || "暂无")}</span>
        </div>
        <div className="fp-item">
          <strong>信号特征</strong>
          <span>
            {signalFeatures
              ? Object.entries(signalFeatures).map(([key, value]) => `${key}: ${String(value)}`).join(" · ")
              : "暂无"}
          </span>
        </div>
      </div>
      <div className="panel-header" style={{ marginTop: 20 }}>
        <div>
          <div className="panel-kicker">Replay Analysis</div>
          <h2>误判归因分析</h2>
        </div>
      </div>
      {analysisLoading ? <div className="loading-state">正在加载整次运行的复盘分析</div> : null}
      {analysisError ? <div className="error-state">{analysisError}</div> : null}
      {analysis ? (
        <>
          <div className="metric-grid" style={{ marginTop: 12 }}>
            <span>样本总数<strong>{analysis.summary.total_count}</strong></span>
            <span>误判样本<strong>{analysis.summary.false_positive_count}</strong></span>
            <span>误判率<strong>{(analysis.summary.false_positive_rate * 100).toFixed(1)}%</strong></span>
            <span>平均净收益<strong className={aShareDirectionClass(analysis.summary.average_net_return)}>
              {analysis.summary.average_net_return.toFixed(2)}%
            </strong></span>
          </div>
          <div className="sub-metrics" style={{ marginTop: 12 }}>
            <div className="sub-metrics-group">
              <span className="group-label">筛选</span>
              <div className="sub-metrics-row">
                <button type="button" className="btn" onClick={handleToggleFalsePositive}>
                  {filterFalsePositive ? "仅看误判中" : "切到仅看误判"}
                </button>
                <select value={reasonFilter} onChange={(event) => handleReasonFilterChange(event.target.value)}>
                  {reasonOptions.map((item) => (
                    <option key={item} value={item}>
                      {item === "all" ? "全部原因" : item}
                    </option>
                  ))}
                </select>
                <select value={categoryFilter} onChange={(event) => handleCategoryFilterChange(event.target.value)}>
                  {categoryOptions.map((item) => (
                    <option key={item} value={item}>
                      {item === "all" ? "全部类别" : categoryLabels.get(item) ?? item}
                    </option>
                  ))}
                </select>
                <button type="button" className="btn" onClick={handleExportRows}>
                  导出当前筛选
                </button>
              </div>
            </div>
          </div>
          <div className="fp-list" style={{ marginTop: 12 }}>
            <div className="fp-item">
              <strong>主要误判原因</strong>
              <span>{(analysis.summary.reason_counts ?? []).slice(0, 3).map((item) => `${item.reason}(${item.count})`).join(" · ") || "暂无"}</span>
            </div>
            <div className="fp-item">
              <strong>主要归因类别</strong>
              <span>{(analysis.summary.category_counts ?? []).slice(0, 3).map((item) => `${item.category_label}(${item.count})`).join(" · ") || "暂无"}</span>
            </div>
            <div className="fp-item">
              <strong>环境分布</strong>
              <span>{(analysis.summary.market_regime_counts ?? []).map((item) => `${item.market_regime}(${item.count})`).join(" · ") || "暂无"}</span>
            </div>
          </div>
          <div className="bt-breakdown-table" style={{ marginTop: 16 }}>
            <h4>复盘样本 <small>当前筛选 {filteredRows.length} 条，导出会包含这一批完整结果</small></h4>
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>标的</th>
                  <th>净收益</th>
                  <th>Outcome</th>
                  <th>归因类别</th>
                  <th>误判原因</th>
                  <th>触发条件</th>
                  <th>环境</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => (
                  <tr key={row.recommendation_id}>
                    <td>{row.trade_date}</td>
                    <td>{row.name || row.symbol}</td>
                    <td className={aShareDirectionClass(row.net_return_pct)}>
                      {row.net_return_pct == null ? "-" : `${row.net_return_pct.toFixed(2)}%`}
                    </td>
                    <td>{outcomeStatusLabel(row.outcome_label, "-")}</td>
                    <td>{textValue(row.false_positive_category_label, "-")}</td>
                    <td>{textValue(row.false_positive_reason, "-")}</td>
                    <td>{textValue(row.trigger_condition_text, "-")}</td>
                    <td>{textValue(row.market_regime, "-")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}

function ValidationWorkspace({ data }: { data: DashboardOverview }) {
  const alerts = data.validation.alerts as { high?: number; medium?: number; low?: number; messages?: string[] } | undefined;
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>验收安全</span>
          <h2>安全验证门禁</h2>
          <p>集中呈现 dry-run、数据池 readiness 和运行风险，防止推荐链路带病上线。</p>
        </div>
        <strong>{businessStatusLabel(data.validation.last_status)}</strong>
      </section>
      <div className="validation-workspace-layout">
        <div className="validation-main-stack">
          <QualityScorecardPanel scorecard={data.quality_scorecard} />
          <EvaluationBreakdownPanel />
        </div>
        <div className="validation-main-stack">
          <BacktestPanel />
        </div>
        <aside className="validation-side-stack">
          <section className="cockpit-panel validation-gate-card">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">Gate</div>
                <h2>门禁摘要</h2>
              </div>
            </div>
            <div className="metric-grid">
              <span>高风险<strong className="negative">{alerts?.high ?? 0}</strong></span>
              <span>中风险<strong>{alerts?.medium ?? 0}</strong></span>
              <span>低风险<strong>{alerts?.low ?? 0}</strong></span>
              <span>运行状态<strong>{runStatusLabel(data.current_run.status)}</strong></span>
              <span>系统总分<strong>{textValue(data.quality_scorecard.overall_score, "-")}</strong></span>
            </div>
          </section>
          <ValidationPanel validation={data.validation} />
          <StatusStream title="验证消息" items={alerts?.messages ?? []} empty="暂无验证消息" />
          <TemporalConsistencyPanel />
          <BacktestEvaluationPanel />
        </aside>
      </div>
    </div>
  );
}

function ExplanationCard({
  item,
  onOpenReplay,
}: {
  item: CockpitRecommendation;
  onOpenReplay: (recommendationId: string) => void;
}) {
  return (
    <article className={`cockpit-panel explanation-card risk-${item.risk_level}`}>
      <div className="candidate-main">
        <div>
          <strong>{item.name}</strong>
          <span>{item.code} · {item.industry || "未分类"}</span>
        </div>
        <div className="score-pill">{item.final_score.toFixed(1)}</div>
      </div>
      <p>{item.rationale || "暂无解释"}</p>
      <div className="mini-table">
        <span>风险<strong>{riskLevelLabel(item.risk_level)}</strong></span>
        <span>共识<strong>{item.consensus_label}</strong></span>
        <span title={quoteSourceTitle(item.data_quality.quote_source_count)}>报价源<strong>{quoteSourceText(item.data_quality.quote_source_count)}</strong></span>
        <span>日K<strong>{businessStatusLabel(item.data_quality.kline_status)}</strong></span>
      </div>
      <div className="candidate-card-actions">
        <button type="button" className="btn primary" onClick={() => onOpenReplay(item.recommendation_id)}>
          进入复盘
        </button>
      </div>
    </article>
  );
}

function quoteSourceText(count: number) {
  return count > 0 ? String(count) : "待标注";
}

function quoteSourceTitle(count: number) {
  return count > 0 ? `当前候选记录包含 ${count} 个报价源` : "当前候选记录未携带单票数据源明细";
}

function StatusStream({ title, items, empty, note }: { title: string; items: string[]; empty: string; note?: string }) {
  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Status</div>
          <h2>{title}</h2>
        </div>
      </div>
      <div className="alert-stream">
        {items.length ? items.slice(0, 8).map((item, index) => <span key={`${title}-${index}-${item}`}>{item}</span>) : <span>{empty}</span>}
      </div>
      {note ? <p className="stream-note">{note}</p> : null}
    </section>
  );
}

function textValue(value: unknown, fallback = "0") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => formatStatusItem(item)).filter((item) => item.trim().length > 0);
}

function formatStatusItem(item: unknown): string {
  if (item === null || item === undefined) return "";
  if (typeof item !== "object") return String(item);
  const example = item as DashboardDataHealthExample;
  if (typeof example === "string") return example;
  const code = textValue(example.code, "").trim();
  const name = textValue(example.name, "").trim();
  const identity = [code, name].filter(Boolean).join(" ");
  const reason = textValue(example.reason, "数据缺口");
  const details = [
    example.bar_count !== undefined && example.bar_count !== null ? `日K ${example.bar_count} 条` : "",
    example.latest_bar_date ? `最新 ${example.latest_bar_date}` : "",
    example.quote_date ? `报价日 ${example.quote_date}` : "",
  ].filter(Boolean);
  return [identity || "未知标的", reason, details.join(" · ")].filter(Boolean).join(" · ");
}

function exportReplayRows(rows: ReplayAnalysisRow[]) {
  const headers = [
    "recommendation_id",
    "trade_date",
    "symbol",
    "name",
    "net_return_pct",
    "outcome_label",
    "user_label",
    "is_false_positive",
    "false_positive_reason",
    "false_positive_category",
    "false_positive_category_label",
    "false_positive_stage",
    "trigger_condition_text",
    "market_regime",
    "trend_status",
    "concept_sector_name",
  ];
  const csv = [
    headers.join(","),
    ...rows.map((row) => headers.map((key) => csvCell((row as Record<string, unknown>)[key])).join(",")),
  ].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "replay-analysis.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

function csvCell(value: unknown) {
  const text = value == null ? "" : String(value);
  return `"${text.replace(/"/g, "\"\"")}"`;
}
