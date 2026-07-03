import { AlertTriangle, BarChart3, DatabaseZap, History, Loader2, Play, RefreshCcw } from "lucide-react";
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
  DashboardDataHealthExample,
  DashboardOverview,
  DashboardDataHealth,
  MetricsOverview,
  RecommendationDetail,
  ReplayAnalysisResult,
  ReplayAnalysisRow,
  Preset,
  RunDetail,
  RunSummary,
} from "../../types";

type ManualRunDataSource = "local_pool" | "local_pool_preferred" | "realtime";
const DEFAULT_RECOMMENDATION_DATA_SOURCE: ManualRunDataSource = "local_pool_preferred";

type DashboardSection = Exclude<CockpitSection, "control" | "matrix">;

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
  if (section === "backtest") return <BacktestWorkspace />;
  if (section === "validation") return <ValidationWorkspace data={data} />;

  return (
    <div className="dashboard-grid">
      <DataHealthBanner dataHealth={data.data_health} />
      <div className="dashboard-column">
        <MarketTemperaturePanel market={data.market} />
        <QualityScorecardPanel scorecard={data.quality_scorecard} />
        <RiskRadarPanel radar={data.risk_radar} />
      </div>
      <div className="dashboard-column">
        <MetricsPanel metrics={metrics ?? ({} as MetricsOverview)} loading={metricsLoading} />
        <RunStatusPanel run={data.current_run} mode="control" />
      </div>
      <div className="dashboard-column">
        <DragonCandidatesPanel items={data.recommendations} emptyState={candidateEmptyState(data)} />
        <DataHealthPanel dataHealth={data.data_health} taskHealth={data.task_health} />
        <ValidationPanel validation={data.validation} />
      </div>
    </div>
  );
}

function DataHealthBanner({ dataHealth }: { dataHealth: DashboardDataHealth }) {
  const canRun = !!dataHealth.can_run_local_recommendation;
  const status = String(dataHealth.status ?? "").toLowerCase();
  // healthy/ok 且可运行 → 不显示；其余状态在顶部提示用户本次推荐可能受影响
  if (canRun && (status === "healthy" || status === "ok")) return null;
  const note = readinessCopy(dataHealth.can_run_local_recommendation, dataHealth.status).note;
  const blocked = !canRun && status === "blocked";
  return (
    <div
      className={`control-banner ${blocked ? "danger" : "warning"}`}
      role="status"
      aria-live="polite"
      style={{ gridColumn: "1 / -1" }}
    >
      <AlertTriangle size={14} />
      <span>
        {blocked ? "数据不足，推荐已暂停" : "数据源降级，推荐结果可能受影响"}：{note}
      </span>
    </div>
  );
}

function BacktestWorkspace() {
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>回测</span>
          <h2>策略回测与收益矩阵</h2>
          <p>按策略聚合历史回测、未来交易日收益率、分钟门禁和版本比较。</p>
        </div>
        <div className="section-hero-actions">
          <strong>Backtest</strong>
        </div>
      </section>
      <BacktestPanel />
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

function goToRunWorkspace() {
  window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "run" } }));
}

function RunWorkspace({ data }: { data: DashboardOverview }) {
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetsLoading, setPresetsLoading] = useState(false);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedRunDataSource, setSelectedRunDataSource] = useState<ManualRunDataSource>(DEFAULT_RECOMMENDATION_DATA_SOURCE);
  const [manualRun, setManualRun] = useState<RunSummary | null>(null);
  const [running, setRunning] = useState(false);
  const [runActionMessage, setRunActionMessage] = useState("");
  const [runActionError, setRunActionError] = useState("");
  const runCopy = runStatusCopy(data.current_run);
  const stages = buildRunTimelineStages(runDetail);
  const stageStates = resolveRunStageStates(
    runDetail?.status ?? data.current_run.status,
    data.current_run.phase ?? runDetail?.progress_phase,
    stages.map((stage) => stage.phases),
  );
  const pipeline = asRecord(asRecord(runDetail?.result_summary).pipeline);
  const selectedPreset = presets.find((preset) => preset.preset_id === selectedPresetId) ?? presets[0] ?? null;

  useEffect(() => {
    let cancelled = false;
    setPresetsLoading(true);
    api.presets()
      .then((rows) => {
        if (cancelled) return;
        setPresets(rows);
        setSelectedPresetId((current) => current || rows[0]?.preset_id || "");
        setSelectedRunDataSource((current) => normalizeManualRunDataSource(current || rows[0]?.data_source));
      })
      .catch((err) => {
        if (!cancelled) setRunActionError(err instanceof Error ? err.message : "预设加载失败");
      })
      .finally(() => {
        if (!cancelled) setPresetsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const runId = data.current_run.run_id;
    if (!runId) {
      setRunDetail(null);
      setDetailLoading(false);
      setDetailError("");
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError("");
    api.runDetail(runId)
      .then((detail) => {
        if (!cancelled) setRunDetail(detail);
      })
      .catch((err) => {
        if (!cancelled) {
          setRunDetail(null);
          setDetailError(err instanceof Error ? err.message : "运行详情加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [data.current_run.run_id]);

  async function startManualRun() {
    if (!selectedPreset || running) return;
    setRunning(true);
    setRunActionMessage(`正在触发推荐运行：${selectedPreset.name}...`);
    setRunActionError("");
    try {
      const result = await api.createRun({
        preset_id: selectedPreset.preset_id,
        run_name: `${selectedPreset.name} · 手动推荐`,
        data_source: selectedRunDataSource,
        async: true,
      });
      setManualRun(result.run);
      setRunActionMessage(`推荐已触发：${result.run.run_id}`);
      window.dispatchEvent(new CustomEvent("ashare:refresh-current-view"));
    } catch (err) {
      setRunActionError(err instanceof Error ? err.message : String(err));
      setRunActionMessage("");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>推荐运行</span>
          <h2>运行编排</h2>
          <p>围绕当前 run 的阶段、心跳、可恢复性和候选产出建立运行视角。</p>
        </div>
        <div className="section-hero-actions">
          <strong>{runCopy.status}</strong>
          <div className="hero-action-row">
            <button type="button" className="control-button subtle" onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "pool" } }))}>
              <BarChart3 size={15} />
              查看候选
            </button>
            <button type="button" className="control-button subtle" onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "history" } }))}>
              <History size={15} />
              查看复盘
            </button>
          </div>
        </div>
      </section>
      <section className="cockpit-panel manual-run-panel">
        <div className="panel-header">
          <div>
            <div className="panel-kicker">推荐执行</div>
            <h2>手动运行推荐</h2>
          </div>
          <button className="control-button subtle" type="button" onClick={() => window.dispatchEvent(new CustomEvent("ashare:refresh-current-view"))}>
            <RefreshCcw size={15} />
            刷新状态
          </button>
        </div>
        <div className="manual-run-grid">
          <label className="control-field">
            <span>运行预设</span>
            <select
              value={selectedPreset?.preset_id ?? ""}
              onChange={(event) => {
                setSelectedPresetId(event.target.value);
                const nextPreset = presets.find((preset) => preset.preset_id === event.target.value);
                setSelectedRunDataSource(normalizeManualRunDataSource(nextPreset?.data_source));
              }}
              disabled={presetsLoading}
            >
              {presets.map((preset) => (
                <option key={preset.preset_id} value={preset.preset_id}>
                  {preset.name} · {dashboardLabelText(preset.run_mode)}
                </option>
              ))}
            </select>
          </label>
          <label className="control-field">
            <span>数据源</span>
            <select
              value={selectedRunDataSource}
              onChange={(event) => setSelectedRunDataSource(event.target.value as ManualRunDataSource)}
              disabled={!selectedPreset}
            >
              <option value="local_pool">仅本地池</option>
              <option value="local_pool_preferred">本地池优先</option>
              <option value="realtime">实时数据</option>
            </select>
          </label>
          <button className="control-button primary wide" type="button" onClick={() => void startManualRun()} disabled={running || !selectedPreset}>
            {running ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
            {running ? "提交中" : "立即运行推荐"}
          </button>
        </div>
        {presetsLoading ? <div className="control-empty">预设加载中</div> : null}
        {selectedPreset ? (
          <div className="mini-table">
            <span>模式<strong>{dashboardLabelText(selectedPreset.run_mode)}</strong></span>
            <span>分数线<strong>{selectedPreset.score_line.toFixed(1)}</strong></span>
            <span>数据源<strong>{dashboardLabelText(selectedRunDataSource)}</strong></span>
            <span>Skill<strong>{selectedPreset.skills.length}</strong></span>
          </div>
        ) : null}
        {manualRun ? <RunStatusCard run={manualRun} /> : null}
      </section>
      {runActionMessage ? <div className="run-action-feedback positive" role="status" aria-live="polite">{runActionMessage}</div> : null}
      {runActionError ? <div className="run-action-feedback danger" role="alert">{runActionError}</div> : null}
      <section className="cockpit-panel run-timeline-panel">
        <div className="panel-header">
          <div>
            <div className="panel-kicker">运行时间线</div>
            <h2>Run Timeline</h2>
          </div>
          <span className={`status-badge ${runStatusBadgeTone(runDetail?.status ?? data.current_run.status)}`}>
            {detailLoading ? "同步中" : runCopy.status}
          </span>
        </div>
        <div className="run-timeline-meta">
          <span>运行名<strong>{runDetail?.run_name ?? data.current_run.run_id ?? "暂无运行"}</strong></span>
          <span>预设<strong>{runDetail?.preset_name ?? "待同步"}</strong></span>
          <span>交易日<strong>{runDetail?.trade_date ?? data.market.trade_date ?? "-"}</strong></span>
          <span>Run ID<strong>{data.current_run.run_id ?? "-"}</strong></span>
        </div>
        {detailError ? <div className="run-action-feedback danger" role="alert">{detailError}</div> : null}
        <div className="timeline-list run-timeline-list">
          {stages.map((stage, index) => (
            <article key={stage.key} className={stageStates[index].className}>
              <strong>{stage.label}</strong>
              <small>{stage.note}</small>
              {stage.metric ? <b className="timeline-metric">{stage.metric}</b> : null}
              <em>{stageStates[index].label}</em>
            </article>
          ))}
        </div>
      </section>
      <div className="focus-grid">
        <RunStatusPanel run={data.current_run} mode="control" />
        <section className="cockpit-panel run-output-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">本次产出</div>
              <h2>运行结果摘要</h2>
            </div>
          </div>
          <div className="metric-grid">
            <span>股票池<strong>{formatRunNumber(runDetail?.universe_count)}</strong></span>
            <span>推荐输出<strong>{formatRunNumber(runDetail?.recommended_count)}</strong></span>
            <span>实际扫描<strong>{formatRunNumber(pipeline.actual_scan_count)}</strong></span>
            <span>Skill 输入<strong>{formatRunNumber(pipeline.skill_input_count)}</strong></span>
            <span>Agent 调用<strong>{formatRunNumber(pipeline.skill_agent_call_count)}</strong></span>
            <span>审查通过<strong>{formatRunNumber(pipeline.skill_review_success_count)}</strong></span>
          </div>
          <div className="data-scope-note">
            <span>{runDetail?.progress_message || data.current_run.message || "暂无运行消息"}</span>
            <span>数据源：{dashboardLabelText(asRecord(runDetail?.result_summary).data_source_mode ?? asRecord(runDetail?.result_summary).data_source ?? "-")}</span>
          </div>
        </section>
      </div>
      <DragonCandidatesPanel items={data.recommendations.slice(0, 6)} emptyState={candidateEmptyState(data)} />
    </div>
  );
}

function buildRunTimelineStages(detail: RunDetail | null) {
  const summary = asRecord(detail?.result_summary);
  const pipeline = asRecord(summary.pipeline);
  return [
    {
      key: "prepare",
      label: "数据池准备",
      note: "本地池、行情源和交易日对齐",
      metric: joinRunMetrics([
        ["统一报价", pipeline.unified_quote_total],
        ["日K覆盖", pipeline.daily_kline_coverage_count],
      ]),
      phases: ["prepare", "starting", "market_data", "auxiliary_data"],
    },
    {
      key: "candidate",
      label: "候选过滤",
      note: "流动性、主线、风险门槛",
      metric: joinRunMetrics([
        ["实际扫描", pipeline.actual_scan_count],
        ["Skill 输入", pipeline.skill_input_count],
      ]),
      phases: ["candidate", "scope_filter", "feature_build", "rule_scan"],
    },
    {
      key: "skill_review",
      label: "Skill 审查",
      note: "规则分、门禁和 Agent 解释",
      metric: joinRunMetrics([
        ["门禁候选", pipeline.skill_gate_candidate_count],
        ["Agent 调用", pipeline.skill_agent_call_count],
        ["通过", pipeline.skill_review_success_count],
      ]),
      phases: ["skill_gate", "skill_review"],
    },
    {
      key: "persist",
      label: "聚合落库",
      note: "分桶、排序、结果持久化与 AI 复盘可读",
      metric: joinRunMetrics([
        ["推荐", detail?.recommended_count],
        ["均分", summary.avg_score],
      ]),
      phases: ["aggregate", "aggregate_metrics", "persist", "completed"],
    },
  ];
}

function joinRunMetrics(items: Array<[string, unknown]>) {
  return items
    .map(([label, value]) => {
      const formatted = formatRunNumber(value);
      return formatted === "-" ? "" : `${label} ${formatted}`;
    })
    .filter(Boolean)
    .join(" · ");
}

function formatRunNumber(value: unknown) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "-";
  return Math.abs(numberValue) >= 100 || Number.isInteger(numberValue)
    ? numberValue.toFixed(0)
    : numberValue.toFixed(1);
}

function runStatusBadgeTone(status: unknown) {
  const statusKey = String(status ?? "").toLowerCase();
  if (statusKey === "completed") return "positive";
  if (statusKey === "failed" || statusKey === "interrupted" || statusKey === "cancelled") return "danger";
  if (statusKey === "running" || statusKey === "queued") return "warning";
  return "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function dashboardLabelText(value: unknown) {
  const key = String(value ?? "");
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    all: "全市场",
    watchlist: "自选股",
    industry: "行业",
    conservative: "保守",
    balanced: "均衡",
    aggressive: "激进",
    local_pool: "仅本地池",
    local_pool_preferred: "本地池优先",
    realtime: "实时数据",
  };
  return labels[key] ?? businessStatusLabel(key, key || "-");
}

function normalizeManualRunDataSource(value: string | null | undefined): ManualRunDataSource {
  if (value === "local_pool_preferred" || value === "realtime") return value;
  return DEFAULT_RECOMMENDATION_DATA_SOURCE;
}

function RunStatusCard({ run }: { run: RunSummary }) {
  const pct = run.progress_pct == null
    ? run.status === "completed" ? 100 : 0
    : Math.max(0, Math.min(100, run.progress_pct > 1 ? run.progress_pct : run.progress_pct * 100));
  const completed = run.status === "completed";
  const message = run.error_message || run.progress_message || `推荐 ${run.recommended_count} / 股票池 ${run.universe_count}`;
  return (
    <div className="run-status-card">
      <div>
        <strong>{run.run_name}</strong>
        <span>{runStatusLabel(run.status)} · {run.trade_date}</span>
      </div>
      <div className="progress-track" aria-label="运行进度">
        <i style={{ width: `${pct}%` }} />
      </div>
      <small>{message}</small>
      <div className="run-card-actions">
        <button type="button" className="control-button subtle" onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "run" } }))}>
          <Play size={14} />
          查看运行
        </button>
        <button type="button" className="control-button subtle" onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "pool" } }))} disabled={!completed}>
          <BarChart3 size={14} />
          候选解释
        </button>
        <button type="button" className="control-button subtle" onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "history" } }))} disabled={!completed}>
          <History size={14} />
          复盘结果
        </button>
      </div>
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
  const emptyState = empty ?? {
    title: "当前没有达标龙头候选",
    reason: "缺少运行归因，不能直接视为市场无机会。",
    detail: "请检查最近一次运行状态、数据池门禁和筛选条件。",
  };
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
      {data.recommendations.length ? (
        <div className={`explain-grid ${data.recommendations.length <= 1 ? "single" : ""}`}>
          {data.recommendations.map((item, index) => (
            <ExplanationCard key={item.recommendation_id} item={item} rank={index + 1} onOpenReplay={openReplay} />
          ))}
        </div>
      ) : (
        <div className="candidate-empty explanation-empty">
          <strong>{emptyState.title}</strong>
          <span>{emptyState.reason}</span>
          <p>{emptyState.detail}</p>
          <button
            className="btn primary candidate-empty-action"
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "run" } }))}
          >
            去推荐运行
          </button>
        </div>
      )}
    </div>
  );
}

function DataWorkspace({ data }: { data: DashboardOverview }) {
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [syncError, setSyncError] = useState("");
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

  async function refreshLocalPool() {
    setSyncing(true);
    setSyncMessage("正在刷新统一报价池...");
    setSyncError("");
    try {
      const quoteResult = await api.refreshLocalPool();
      setSyncMessage(`报价池已刷新 ${quoteResult.refreshed} 条；正在检查候选池日K缺口...`);
      const gapSyncResult = await api.syncLocalPoolGaps({
        code_limit: 0,
        days: 90,
        min_bars: 30,
        dry_run: false,
      });
      setSyncMessage(dataHealthSyncSummary(quoteResult.refreshed, gapSyncResult));
      window.dispatchEvent(new CustomEvent("ashare:refresh-current-view"));
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : "数据同步失败");
      setSyncMessage("");
    } finally {
      setSyncing(false);
    }
  }

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
          <section className="cockpit-panel data-sync-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">数据执行</div>
                <h2>本地数据同步</h2>
              </div>
              <button className="control-button primary" type="button" onClick={() => void refreshLocalPool()} disabled={syncing}>
                {syncing ? <Loader2 size={15} className="spin" /> : <DatabaseZap size={15} />}
                {syncing ? "同步中" : "刷新本地数据池"}
              </button>
            </div>
            <div className="metric-grid">
              <span>活跃报价<strong>{textValue(data.data_health.active_quote_count)}</strong></span>
              <span>合格日K<strong>{textValue(data.data_health.eligible_quote_count)}</strong></span>
              <span>候选检查<strong>{checkedCandidates}</strong></span>
              <span>覆盖完整<strong>{readyCandidates}</strong></span>
            </div>
            <div className="control-note">
              <DatabaseZap size={15} />
              <span>刷新会先更新统一报价池，再全量检查候选池并补齐可处理的日K缺口；样例展示不限制实际补齐范围。</span>
            </div>
            {syncMessage ? <div className="run-action-feedback positive" role="status" aria-live="polite">{syncMessage}</div> : null}
            {syncError ? <div className="run-action-feedback danger" role="alert">{syncError}</div> : null}
          </section>
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
          {/* TemporalConsistencyPanel removed: duplicate mount in ValidationWorkspace; redundant on data page */}
          <StatusStream title="数据池消息" items={messages} empty="暂无数据池消息" />
          <StatusStream title="缺口样例" items={examples} empty="暂无缺口样例" note={exampleNote} />
        </aside>
      </div>
    </div>
  );
}

function dataHealthSyncSummary(refreshedQuotes: number, gapSync: { target_count?: number; processed_count?: number; succeeded_count?: number; unresolved_count?: number; failed_count?: number; row_count?: number; after?: { gap_count?: number } }) {
  const failed = Number(gapSync.failed_count ?? 0);
  const unresolved = Number(gapSync.unresolved_count ?? 0);
  const remaining = Number(gapSync.after?.gap_count ?? 0);
  const status = failed > 0 || unresolved > 0 || remaining > 0 ? "部分同步完成" : "同步完成";
  return `${status}：报价 ${refreshedQuotes} 条，目标 ${gapSync.target_count ?? 0} 只，已处理 ${gapSync.processed_count ?? 0} 只，成功 ${gapSync.succeeded_count ?? 0} 只，写入 ${gapSync.row_count ?? 0} 行，剩余缺口 ${remaining} 只`;
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
          <h2>AI 复盘结果</h2>
          <p>从当前推荐出发，聚合 outcome、误判归因和运行级复盘分析，保持策略可学习。</p>
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
            )) : (
              <div className="cockpit-panel empty-state actionable">
                <strong>暂无可复盘推荐</strong>
                <span>当前没有候选样本可加载复盘详情。</span>
                <div className="state-actions">
                  <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "pool" } }))}>
                    查看候选解释
                  </button>
                  <button type="button" onClick={goToRunWorkspace}>
                    运行推荐
                  </button>
                </div>
              </div>
            )}
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
      ? "查看 AI 复盘归因和 outcome 结果"
      : "等待后续 outcome 后再看 AI 复盘"
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
    return (
      <section className="cockpit-panel empty-state actionable">
        <strong>选择一条推荐查看复盘详情</strong>
        <span>如果当前队列为空，可以先回到候选解释或推荐运行页产出新推荐。</span>
        <div className="state-actions">
          <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "pool" } }))}>
            查看候选解释
          </button>
          <button type="button" onClick={goToRunWorkspace}>
            运行推荐
          </button>
        </div>
      </section>
    );
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
          <strong>AI 复盘结论</strong>
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
  rank,
  onOpenReplay,
}: {
  item: CockpitRecommendation;
  rank: number;
  onOpenReplay: (recommendationId: string) => void;
}) {
  return (
    <article className={`cockpit-panel explanation-card risk-${item.risk_level}`}>
      <div className="explanation-card-header">
        <div className="candidate-rank">{String(rank).padStart(2, "0")}</div>
        <div className="candidate-main">
          <div>
            <strong>{item.name}</strong>
            <span>{item.code} · {item.industry || "未分类"}</span>
          </div>
          <div className="score-pill">{item.final_score.toFixed(1)}</div>
        </div>
      </div>
      <p>{item.rationale || "暂无解释"}</p>
      <div className="mini-table">
        <span>买点<strong>{item.setup_type || "未识别买点"}</strong></span>
        <span>Gap<strong>{item.score_gap == null ? "-" : item.score_gap.toFixed(1)}</strong></span>
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
