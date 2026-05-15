import {
  ArrowDown,
  ArrowUp,
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Clock3,
  DatabaseZap,
  HardDrive,
  Eye,
  EyeOff,
  Flame,
  History,
  Layers3,
  Loader2,
  NotebookPen,
  PauseCircle,
  Play,
  Plus,
  RefreshCcw,
  Target,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent, type MouseEvent } from "react";
import { api } from "./api/client";
import type {
  HomeBootstrap,
  DataSourceHealthRow,
  MarketSnapshot,
  LocalPoolStatus,
  UnifiedQuotePoolRow,
  Preset,
  PresetDraft,
  PresetSkillDraft,
  PresetStatsRow,
  RecommendationCard,
  RecommendationDetail,
  RunMode,
  RunTarget,
  RunSummary,
  ScheduledTask,
  ScheduledTaskPayload,
  ScheduledTaskType,
  SkillDetail,
  SkillMode,
  SkillStatsRow,
  SkillSummary,
  StatsOverview,
} from "./types";
import SkillConfigPanel from "./components/SkillConfigPanel";
import MarketSnapshotPanel from "./components/MarketSnapshot";
import RecommendationList from "./components/RecommendationList";
import DataSourcePanel from "./components/DataSourcePanel";
import RunHistoryPanel, { type RunHistoryMode } from "./components/RunHistoryPanel";
import BacktestPanel from "./components/BacktestPanel";

type Section = "desk" | "pool" | "runs" | "automation" | "skills" | "sources" | "localPool" | "stats" | "backtest";

type ReviewDraft = {
  user_label: string;
  failure_reason: string;
  review_text: string;
};

type OutcomeDraft = {
  outcome_label: string;
  outcome_reason: string;
  horizon_days: number;
};

type PoolFilters = {
  bucket: string;
  industry: string;
  skillId: string;
  watchOnly: boolean;
  hideIgnored: boolean;
  hideRepeat: boolean;
};

type ScheduledTaskDraft = {
  task_id: string;
  name: string;
  task_type: ScheduledTaskType;
  trigger_time: string;
  preset_id: string;
  enabled: boolean;
};

const DEFAULT_BUCKET_LIMITS: Record<string, number> = {
  "容量核心": 3,
  "高弹观察": 2,
};

const EMPTY_PRESET_DRAFT: PresetDraft = {
  name: "",
  description: "",
  run_mode: "formal",
  market_scope: "all",
  market_scope_value: "",
  risk_template: "balanced",
  output_count: 6,
  data_source: "local_pool",
  hide_recent_repeats: true,
};

const EMPTY_REVIEW_DRAFT: ReviewDraft = {
  user_label: "",
  failure_reason: "",
  review_text: "",
};

const EMPTY_OUTCOME_DRAFT: OutcomeDraft = {
  outcome_label: "pending",
  outcome_reason: "",
  horizon_days: 5,
};

const EMPTY_POOL_FILTERS: PoolFilters = {
  bucket: "all",
  industry: "all",
  skillId: "all",
  watchOnly: false,
  hideIgnored: false,
  hideRepeat: false,
};

const ALL_POOL_RUNS_VALUE = "__all_pool_runs__";

const EMPTY_TASK_DRAFT: ScheduledTaskDraft = {
  task_id: "",
  name: "",
  task_type: "recommendation_run",
  trigger_time: "07:00",
  preset_id: "",
  enabled: true,
};

export default function App() {
  const [authReady, setAuthReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [section, setSection] = useState<Section>("desk");
  const [bootstrap, setBootstrap] = useState<HomeBootstrap | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [dataSources, setDataSources] = useState<DataSourceHealthRow[]>([]);
  const [scheduledTasks, setScheduledTasks] = useState<ScheduledTask[]>([]);
  const [localPool, setLocalPool] = useState<LocalPoolStatus | null>(null);
  const [statsMode, setStatsMode] = useState<RunMode>("formal");
  const [poolMode, setPoolMode] = useState<RunMode>("formal");
  const [runHistoryMode, setRunHistoryMode] = useState<RunMode | "all">("all");
  const [runTarget, setRunTarget] = useState<RunTarget>("preset");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [selectedRecommendationId, setSelectedRecommendationId] = useState("");
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [singleSkillId, setSingleSkillId] = useState("");
  const [singleSkillWeight, setSingleSkillWeight] = useState(1);
  const [installSource, setInstallSource] = useState("");
  const [installSkillId, setInstallSkillId] = useState("chen-xiaoqun-skill");
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [presetDraft, setPresetDraft] = useState<PresetDraft>(EMPTY_PRESET_DRAFT);
  const [presetSkillDrafts, setPresetSkillDrafts] = useState<PresetSkillDraft[]>([]);
  const [bucketLimits, setBucketLimits] = useState<Record<string, number>>({ ...DEFAULT_BUCKET_LIMITS });
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [taskDraft, setTaskDraft] = useState<ScheduledTaskDraft>(EMPTY_TASK_DRAFT);
  const [poolFilters, setPoolFilters] = useState<PoolFilters>(EMPTY_POOL_FILTERS);
  const [recommendations, setRecommendations] = useState<RecommendationCard[]>([]);
  const [recommendationDetail, setRecommendationDetail] = useState<RecommendationDetail | null>(null);
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null);
  const [reviewDraft, setReviewDraft] = useState<ReviewDraft>(EMPTY_REVIEW_DRAFT);
  const [outcomeDraft, setOutcomeDraft] = useState<OutcomeDraft>(EMPTY_OUTCOME_DRAFT);
  const [overview, setOverview] = useState<StatsOverview | null>(null);
  const [statsBySkill, setStatsBySkill] = useState<SkillStatsRow[]>([]);
  const [statsByPreset, setStatsByPreset] = useState<PresetStatsRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [poolRefreshing, setPoolRefreshing] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selectedPreset = useMemo(
    () => presets.find((preset) => preset.preset_id === selectedPresetId) ?? presets[0] ?? null,
    [presets, selectedPresetId],
  );
  const selectedSkillSummary = useMemo(
    () => skills.find((skill) => skill.skill_id === selectedSkillId) ?? null,
    [skills, selectedSkillId],
  );
  const visibleRuns = useMemo(() => runs.filter((run) => run.run_mode === poolMode), [runs, poolMode]);
  const selectedRun = useMemo(
    () => visibleRuns.find((run) => run.run_id === selectedRunId) ?? null,
    [visibleRuns, selectedRunId],
  );
  const poolRunOptions = useMemo(
    () => [
      { value: ALL_POOL_RUNS_VALUE, label: "全部" },
      ...visibleRuns.map((run) => ({
        value: run.run_id,
        label: `${run.run_name} · ${run.trade_date}`,
      })),
    ],
    [visibleRuns],
  );
  const poolTitleSubtitle =
    selectedRunId === ALL_POOL_RUNS_VALUE ? `全部 Run · ${displayText(poolMode)}` : selectedRun?.run_name ?? "暂无 Run";
  const selectedRecommendation =
    recommendationDetail?.recommendation_id === selectedRecommendationId ? recommendationDetail : null;
  const hasActiveRun = useMemo(
    () => runs.some((run) => run.status === "queued" || run.status === "running"),
    [runs],
  );
  const availableBuckets = useMemo(
    () => Array.from(new Set(recommendations.map((item) => item.bucket))).sort(),
    [recommendations],
  );
  const availableIndustries = useMemo(
    () => Array.from(new Set(recommendations.map((item) => item.industry).filter(Boolean) as string[])).sort(),
    [recommendations],
  );
  const filteredRecommendations = useMemo(() => {
    return recommendations.filter((item) => {
      if (poolFilters.bucket !== "all" && item.bucket !== poolFilters.bucket) return false;
      if (poolFilters.industry !== "all" && item.industry !== poolFilters.industry) return false;
      if (
        poolFilters.skillId !== "all" &&
        !item.skill_breakdown.some((signal) => signal.strategy_id === poolFilters.skillId)
      ) {
        return false;
      }
      if (poolFilters.watchOnly && item.watch_status !== "watching") return false;
      if (poolFilters.hideIgnored && item.watch_status === "ignored") return false;
      if (poolFilters.hideRepeat && item.recent_repeat) return false;
      return true;
    });
  }, [poolFilters, recommendations]);

  useEffect(() => {
    void prepareAuth();
  }, []);

  useEffect(() => {
    if (authenticated) {
      void bootstrapApp();
    }
  }, [authenticated]);

  useEffect(() => {
    if (authenticated && section === "automation") {
      void loadScheduledTasks();
    }
  }, [authenticated, section]);

  useEffect(() => {
    if (!selectedPresetId && presets[0]) {
      setSelectedPresetId(presets[0].preset_id);
    }
  }, [presets, selectedPresetId]);

  useEffect(() => {
    if (!taskDraft.preset_id && !editingTaskId && presets[0]) {
      setTaskDraft((current) => ({ ...current, preset_id: presets[0].preset_id }));
    }
  }, [editingTaskId, presets, taskDraft.preset_id]);

  useEffect(() => {
    if (!selectedSkillId && skills[0]) {
      setSelectedSkillId(skills[0].skill_id);
    }
    if (!singleSkillId && skills[0]) {
      setSingleSkillId(skills[0].skill_id);
    }
  }, [selectedSkillId, singleSkillId, skills]);

  useEffect(() => {
    if (selectedRunId === ALL_POOL_RUNS_VALUE) return;
    if (!visibleRuns.length) {
      setSelectedRunId(ALL_POOL_RUNS_VALUE);
      return;
    }
    if (!visibleRuns.some((run) => run.run_id === selectedRunId)) {
      setSelectedRunId(visibleRuns[0].run_id);
    }
  }, [selectedRunId, visibleRuns]);

  useEffect(() => {
    if (selectedRunId === ALL_POOL_RUNS_VALUE) {
      void loadPoolRecommendations(ALL_POOL_RUNS_VALUE, visibleRuns);
      return;
    }
    if (!selectedRunId) return;
    if (!visibleRuns.some((run) => run.run_id === selectedRunId)) return;
    void loadPoolRecommendations(selectedRunId, visibleRuns);
  }, [selectedRunId, visibleRuns]);

  useEffect(() => {
    if (!selectedRecommendationId) {
      setRecommendationDetail(null);
      return;
    }
    setRecommendationDetail(null);
    void loadRecommendationDetail(selectedRecommendationId);
  }, [selectedRecommendationId]);

  useEffect(() => {
    if (!selectedSkillId) {
      setSkillDetail(null);
      return;
    }
    setSkillDetail(null);
    void loadSkillDetail(selectedSkillId);
  }, [selectedSkillId]);

  useEffect(() => {
    if (!selectedPreset) return;
    syncDraftFromPreset(selectedPreset);
  }, [selectedPreset]);

  useEffect(() => {
    if (authenticated) {
      void loadStats(statsMode);
    }
  }, [authenticated, statsMode]);

  useEffect(() => {
    if (!authenticated || !hasActiveRun) return;
    const timer = window.setInterval(() => {
      void refreshRunsOnly();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [authenticated, hasActiveRun]);

  async function prepareAuth() {
    setAuthReady(false);
    try {
      const status = await api.authStatus();
      const isAuthenticated = !status.enabled || status.authenticated;
      setAuthenticated(isAuthenticated);
      if (!isAuthenticated) {
        setBootstrap(null);
      }
    } catch (err) {
      setLoginError(messageFromError(err));
    } finally {
      setAuthReady(true);
    }
  }

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginLoading(true);
    setLoginError("");
    try {
      await api.login(loginPassword);
      setAuthenticated(true);
      setLoginPassword("");
    } catch (err) {
      setLoginError(messageFromError(err));
    } finally {
      setLoginLoading(false);
    }
  }

  async function bootstrapApp() {
    if (!authenticated) return;
    setLoading(true);
    setError("");
    try {
      const [home, presetList, runList, skillList, sourceRows, taskRows, poolStatus] = await Promise.all([
        api.bootstrap(),
        api.presets(),
        api.runs(),
        api.skills(),
        api.dataSources(),
        api.scheduledTasks(),
        api.localPool(),
      ]);
      setBootstrap(home);
      setPresets(presetList);
      setRuns(runList);
      setSkills(skillList);
      setDataSources(sourceRows);
      setScheduledTasks(taskRows);
      setLocalPool(poolStatus);
      setSelectedPresetId((current) => current || presetList[0]?.preset_id || "");
      setSelectedSkillId((current) => current || skillList[0]?.skill_id || "");
      setSingleSkillId((current) => current || skillList[0]?.skill_id || "");
      const initialRun = runList.find((run) => run.run_mode === poolMode);
      setSelectedRunId((current) => {
        if (current === ALL_POOL_RUNS_VALUE) return current;
        if (current && runList.some((run) => run.run_id === current && run.run_mode === poolMode)) return current;
        return initialRun?.run_id ?? ALL_POOL_RUNS_VALUE;
      });
      await loadStats(statsMode);
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadStats(mode: RunMode) {
    try {
      const [overviewRow, bySkill, byPreset] = await Promise.all([
        api.statsOverview(mode),
        api.statsBySkill(mode),
        api.statsByPreset(mode),
      ]);
      setOverview(overviewRow);
      setStatsBySkill(bySkill);
      setStatsByPreset(byPreset);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function loadDataSources() {
    try {
      const [rows, poolStatus] = await Promise.all([api.dataSources(), api.localPool()]);
      setDataSources(rows);
      setLocalPool(poolStatus);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function loadScheduledTasks() {
    try {
      setScheduledTasks(await api.scheduledTasks());
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function loadLocalPool(params?: { u_limit?: number; u_offset?: number; b_limit?: number; b_offset?: number }) {
    try {
      const poolStatus = await api.localPool(params);
      setLocalPool(poolStatus);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function refreshLocalPool() {
    setPoolRefreshing(true);
    setError("");
    try {
      const result = await api.refreshLocalPool();
      await loadLocalPool();
      setMessage(`本地数据池已刷新：${result.refreshed} 条报价`);
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setPoolRefreshing(false);
    }
  }

  async function loadPoolRecommendations(runSelection: string, runList: RunSummary[] = visibleRuns) {
    try {
      const recs =
        runSelection === ALL_POOL_RUNS_VALUE
          ? (await Promise.all(runList.map((run) => api.runRecommendations(run.run_id))))
              .flat()
              .sort((left, right) => {
                const dateOrder = right.trade_date.localeCompare(left.trade_date);
                if (dateOrder !== 0) return dateOrder;
                return right.final_score - left.final_score;
              })
          : await api.runRecommendations(runSelection);
      setRecommendations(recs);
      setSelectedRecommendationId((current) => {
        if (current && recs.some((item) => item.recommendation_id === current)) return current;
        return recs[0]?.recommendation_id ?? "";
      });
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function refreshSelectedPoolRecommendations() {
    await loadPoolRecommendations(selectedRunId || ALL_POOL_RUNS_VALUE, visibleRuns);
  }

  async function loadRunRecommendations(runId: string) {
    await loadPoolRecommendations(runId, visibleRuns);
  }

  async function loadRecommendationDetail(recommendationId: string) {
    try {
      const detail = await api.recommendationDetail(recommendationId);
      setRecommendationDetail(detail);
      setReviewDraft({
        user_label: detail.review_note?.user_label ?? "",
        failure_reason: detail.review_note?.failure_reason ?? "",
        review_text: detail.review_note?.review_text ?? "",
      });
      setOutcomeDraft({
        outcome_label: detail.outcome?.outcome_label ?? "pending",
        outcome_reason: detail.outcome?.outcome_reason ?? "",
        horizon_days: detail.outcome?.horizon_days ?? 5,
      });
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function loadSkillDetail(skillId: string) {
    try {
      const detail = await api.skillDetail(skillId);
      setSkillDetail(detail);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  function syncDraftFromPreset(preset: Preset) {
    setEditingPresetId(preset.preset_id);
    setPresetDraft({
      name: preset.name,
      description: preset.description ?? "",
      run_mode: preset.run_mode,
      market_scope: preset.market_scope,
      market_scope_value: preset.market_scope_value ?? "",
      risk_template: preset.risk_template,
      output_count: preset.output_count,
      data_source: (preset as any).data_source ?? "local_pool",
      hide_recent_repeats: preset.hide_recent_repeats,
    });
    setPresetSkillDrafts(
      preset.skills.map((skill, index) => ({
        skill_id: skill.skill_id,
        weight: skill.weight,
        sort_order: skill.sort_order ?? index,
      })),
    );
    setBucketLimits({ ...preset.bucket_limits });
  }

  function resetToNewPreset() {
    setEditingPresetId(null);
    setPresetDraft(EMPTY_PRESET_DRAFT);
    setPresetSkillDrafts(
      skills[0] ? [{ skill_id: skills[0].skill_id, weight: 1, sort_order: 0 }] : [],
    );
    setBucketLimits({ ...DEFAULT_BUCKET_LIMITS });
  }

  function editScheduledTask(task: ScheduledTask) {
    const payload = task.payload ?? {};
    setEditingTaskId(task.task_id);
    setTaskDraft({
      task_id: task.task_id,
      name: task.name,
      task_type: task.task_type,
      trigger_time: task.trigger_time ?? cronToClockTime(task.cron_expression) ?? "07:00",
      preset_id: String(payload.preset_id ?? ""),
      enabled: task.enabled,
    });
  }

  function resetScheduledTaskDraft() {
    setEditingTaskId(null);
    setTaskDraft({
      ...EMPTY_TASK_DRAFT,
      preset_id: presets[0]?.preset_id ?? "",
    });
  }

  function updatePresetSkill(skillId: string, patch: Partial<PresetSkillDraft>) {
    setPresetSkillDrafts((current) =>
      current.map((item) => (item.skill_id === skillId ? { ...item, ...patch } : item)),
    );
  }

  function togglePresetSkill(skillId: string) {
    setPresetSkillDrafts((current) => {
      const existing = current.find((item) => item.skill_id === skillId);
      if (existing) {
        return current.filter((item) => item.skill_id !== skillId);
      }
      return [...current, { skill_id: skillId, weight: 1, sort_order: current.length }];
    });
  }

  async function savePreset() {
    setLoading(true);
    setError("");
    try {
      const payload = {
        name: presetDraft.name.trim() || "未命名预设",
        description: presetDraft.description.trim() || null,
        run_mode: presetDraft.run_mode,
        market_scope: presetDraft.market_scope,
        market_scope_value: presetDraft.market_scope_value.trim() || null,
        risk_template: presetDraft.risk_template,
        output_count: presetDraft.output_count,
        hide_recent_repeats: presetDraft.hide_recent_repeats,
        data_source: presetDraft.data_source,
        bucket_limits: bucketLimits,
        skills: presetSkillDrafts.map((item, index) => ({
          skill_id: item.skill_id,
          weight: item.weight,
          sort_order: item.sort_order ?? index,
        })),
      };
      if (!payload.skills.length) {
        setError("至少选择一个 Skill");
        setLoading(false);
        return;
      }
      if (editingPresetId) {
        const updated = await api.updatePreset(editingPresetId, payload);
        setPresets((current) => current.map((preset) => (preset.preset_id === updated.preset_id ? updated : preset)));
        setSelectedPresetId(updated.preset_id);
        syncDraftFromPreset(updated);
        setMessage("预设已更新");
      } else {
        const created = await api.createPreset(payload);
        setPresets((current) => [...current.filter((preset) => preset.preset_id !== created.preset_id), created]);
        setSelectedPresetId(created.preset_id);
        syncDraftFromPreset(created);
        setMessage("预设已创建");
      }
      await refreshHomeData();
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setLoading(false);
    }
  }

  async function duplicatePreset() {
    if (!selectedPreset) return;
    setLoading(true);
    setError("");
    try {
      const copy = await api.createPreset({
        name: `${selectedPreset.name} 副本`,
        description: selectedPreset.description ?? null,
        run_mode: selectedPreset.run_mode,
        market_scope: selectedPreset.market_scope,
        market_scope_value: selectedPreset.market_scope_value ?? null,
        risk_template: selectedPreset.risk_template,
        output_count: selectedPreset.output_count,
        hide_recent_repeats: selectedPreset.hide_recent_repeats,
        data_source: (selectedPreset as any).data_source ?? "local_pool",
        bucket_limits: selectedPreset.bucket_limits,
        skills: selectedPreset.skills.map((skill, index) => ({
          skill_id: skill.skill_id,
          weight: skill.weight,
          sort_order: index,
        })),
      });
      await refreshHomeData();
      setSelectedPresetId(copy.preset_id);
      setMessage("预设已复制");
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setLoading(false);
    }
  }

  async function refreshHomeData() {
    const [home, presetList, runList, skillList, sourceRows, taskRows, poolStatus] = await Promise.all([
      api.bootstrap(),
      api.presets(),
      api.runs(),
      api.skills(),
      api.dataSources(),
      api.scheduledTasks(),
      api.localPool(),
    ]);
    setBootstrap(home);
    setPresets(presetList);
    setRuns(runList);
    setSkills(skillList);
    setDataSources(sourceRows);
    setScheduledTasks(taskRows);
    setLocalPool(poolStatus);
  }

  async function refreshRunsOnly() {
    try {
      const runList = await api.runs();
      setRuns(runList);
    } catch (err) {
      console.warn("refresh runs failed", err);
    }
  }

  async function runCurrentSelection() {
    setRunning(true);
    setError("");
    try {
      const payload =
        runTarget === "preset"
          ? {
              preset_id: selectedPreset?.preset_id,
              run_mode: selectedPreset?.run_mode ?? presetDraft.run_mode,
              run_name: selectedPreset?.name || presetDraft.name || "预设运行",
            }
          : {
              run_mode: "experiment",
              run_name: "单 Skill 实验",
              single_skill_id: singleSkillId,
              single_skill_weight: singleSkillWeight,
            };
      const result = await api.createRun(payload);
      setRuns((current) => [result.run, ...current.filter((run) => run.run_id !== result.run.run_id)]);
      setPoolMode(result.run.run_mode);
      setSelectedRunId(result.run.run_id);
      setRecommendations(result.recommendations);
      setSelectedRecommendationId(result.recommendations[0]?.recommendation_id ?? "");
      setSection(result.run.status === "completed" ? "pool" : "runs");
      await refreshHomeData();
      await loadStats(statsMode);
      if (result.run.status === "completed") {
        setMessage("运行完成");
      } else {
        setMessage("运行已启动，Agent 审查在后台继续");
        void pollRunUntilDone(result.run.run_id);
      }
    } catch (err) {
      setError(messageFromError(err));
      await refreshHomeData();
    } finally {
      setRunning(false);
    }
  }

  async function pollRunUntilDone(runId: string) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await delay(2000);
      try {
        const run = await api.runDetail(runId);
        setRuns((current) => [run, ...current.filter((item) => item.run_id !== run.run_id)]);
        setSelectedRunId(run.run_id);
        if (run.status === "completed") {
          setPoolMode(run.run_mode);
          await loadRunRecommendations(run.run_id);
          await refreshHomeData();
          await loadStats(statsMode);
          setSection("pool");
          setMessage("后台审查完成，机会池已更新");
          return;
        }
        if (run.status === "failed" || run.status === "interrupted") {
          await refreshHomeData();
          setSection("runs");
          setError(run.error_message || (run.status === "interrupted" ? "运行已中断，请重新发起" : "运行失败"));
          return;
        }
      } catch (err) {
        setError(messageFromError(err));
        return;
      }
    }
    setMessage("运行仍在后台处理中，可在运行记录查看进度");
  }

  async function saveReview() {
    if (!selectedRecommendation) return;
    try {
      await api.reviewRecommendation(selectedRecommendation.recommendation_id, reviewDraft);
      await loadRecommendationDetail(selectedRecommendation.recommendation_id);
      await loadStats(statsMode);
      setMessage("复盘已保存");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function saveOutcome() {
    if (!selectedRecommendation) return;
    try {
      await api.labelOutcome(selectedRecommendation.recommendation_id, outcomeDraft);
      await refreshSelectedPoolRecommendations();
      await loadRecommendationDetail(selectedRecommendation.recommendation_id);
      await loadStats(statsMode);
      setMessage("结果已标注");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function updateWatchStatus(status: string) {
    if (!selectedRecommendation) return;
    try {
      if (status === "ignored") {
        await api.ignoreRecommendation(selectedRecommendation.recommendation_id, {});
      } else {
        await api.watchRecommendation(selectedRecommendation.recommendation_id, { watch_status: status });
      }
      await refreshSelectedPoolRecommendations();
      await loadRecommendationDetail(selectedRecommendation.recommendation_id);
      await loadStats(statsMode);
      setMessage(status === "watching" ? "已加入观察" : "已忽略此票");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function watchById(id: string, status: string) {
    try {
      if (status === "ignored") {
        await api.ignoreRecommendation(id, {});
      } else {
        await api.watchRecommendation(id, { watch_status: status });
      }
      await refreshSelectedPoolRecommendations();
      if (selectedRecommendationId === id) {
        await loadRecommendationDetail(id);
      }
      await loadStats(statsMode);
      setMessage(status === "watching" ? "已加入观察" : "已忽略此票");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function setSkillMode(mode: SkillMode) {
    if (!selectedSkillSummary && !skillDetail) return;
    try {
      const skillId = selectedSkillSummary?.skill_id ?? skillDetail?.skill_id;
      if (!skillId) return;
      await api.setSkillMode(skillId, mode);
      await refreshHomeData();
      await loadSkillDetail(skillId);
      setMessage(`Skill 已切换为 ${displayText(mode)}`);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function toggleDataSource(row: DataSourceHealthRow) {
    try {
      await api.setDataSourceEnabled(row.source_type, row.source, !row.enabled);
      await loadDataSources();
      setMessage(`${row.source} ${row.enabled ? "已关闭" : "已启用"}`);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function backtestSkill() {
    if (!selectedSkillSummary && !skillDetail) return;
    try {
      const skillId = selectedSkillSummary?.skill_id ?? skillDetail?.skill_id;
      if (!skillId) return;
      await api.backtestSkill(skillId);
      await loadSkillDetail(skillId);
      setMessage("已触发 Skill 回测");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function installSkill() {
    if (!installSource.trim()) {
      setError("请输入 Skill 来源");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const detail = await api.installSkill({ source: installSource.trim(), skill_id: installSkillId.trim() || undefined });
      await refreshHomeData();
      setSelectedSkillId(detail.skill_id);
      setMessage("Skill 已安装");
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setLoading(false);
    }
  }

  async function autoLabelPending() {
    try {
      await api.autoLabelOutcomes({ horizon_days: 5, run_mode: statsMode });
      await loadStats(statsMode);
      setMessage("已完成批量回填");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function saveScheduledTask() {
    setError("");
    if (!taskDraft.name.trim()) {
      setError("请输入任务名称");
      return;
    }
    if (!taskDraft.trigger_time) {
      setError("请选择触发时间");
      return;
    }
    if (taskDraft.task_type === "recommendation_run" && taskDraft.enabled && !taskDraft.preset_id) {
      setError("请选择策略预设");
      return;
    }

    const taskId = editingTaskId ?? (taskDraft.task_id || makeTaskId(taskDraft));
    const existingTask = editingTaskId ? scheduledTasks.find((task) => task.task_id === editingTaskId) : null;
    const payload: ScheduledTaskPayload = {
      task_id: taskId,
      name: taskDraft.name.trim(),
      task_type: taskDraft.task_type,
      trigger_time: taskDraft.trigger_time,
      payload:
        taskDraft.task_type === "recommendation_run"
          ? {
              preset_id: taskDraft.preset_id,
              run_name: `${taskDraft.name.trim()} · 自动推荐`,
            }
          : taskDraft.task_type === "data_backfill"
            ? { ...(existingTask?.payload ?? {}) }
          : {},
      enabled: taskDraft.enabled,
    };
    if (
      existingTask &&
      !existingTask.trigger_time &&
      existingTask.cron_expression &&
      taskDraft.trigger_time === cronToClockTime(existingTask.cron_expression)
    ) {
      payload.cron_expression = existingTask.cron_expression;
      delete payload.trigger_time;
    }

    try {
      const saved = editingTaskId
        ? await api.updateScheduledTask(editingTaskId, payload)
        : await api.createScheduledTask(payload);
      await loadScheduledTasks();
      editScheduledTask(saved);
      setMessage(editingTaskId ? "自动任务已更新" : "自动任务已创建");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function toggleScheduledTask(task: ScheduledTask) {
    const payload = taskToPayload(task, presets);
    try {
      await api.updateScheduledTask(task.task_id, { ...payload, enabled: !task.enabled });
      await loadScheduledTasks();
      setMessage(task.enabled ? "任务已停用" : "任务已启用");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function runScheduledTaskNow(task: ScheduledTask) {
    try {
      await api.runScheduledTask(task.task_id);
      await bootstrapApp();
      setMessage("任务已执行");
    } catch (err) {
      setError(messageFromError(err));
      await loadScheduledTasks();
    }
  }

  async function deleteScheduledTask(taskId: string) {
    if (!confirm("确定删除这个自动任务？")) return;
    try {
      await api.deleteScheduledTask(taskId);
      if (editingTaskId === taskId) resetScheduledTaskDraft();
      await loadScheduledTasks();
      setMessage("自动任务已删除");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function deleteRun(runId: string) {
    if (!confirm("确定删除此运行记录？将同时删除关联的推荐、结果和审查数据。")) return;
    try {
      await api.deleteRun(runId);
      setMessage(`已删除运行记录 ${runId}`);
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      await bootstrapApp();
    }
  }

  async function deleteRecommendation(recId: string) {
    if (!confirm("确定删除此推荐？")) return;
    try {
      await api.deleteRecommendation(recId);
      setMessage("已删除推荐");
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      await refreshSelectedPoolRecommendations();
    }
  }

  async function deletePreset() {
    if (!selectedPresetId) return;
    if (!confirm(`确定删除预设 "${presets.find(p => p.preset_id === selectedPresetId)?.name || selectedPresetId}"？`)) return;
    try {
      await api.deletePreset(selectedPresetId);
      setMessage("已删除预设");
      setSelectedPresetId("");
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      await bootstrapApp();
    }
  }

  if (!authReady) {
    return (
      <div className="auth-screen">
        <div className="auth-panel">
          <div className="brand auth-brand">
            <div className="brand-mark">A</div>
            <div>
              <strong>A股短线工作台</strong>
              <span>正在校验访问状态</span>
            </div>
          </div>
          <Loader2 size={22} className="spin" />
        </div>
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="auth-screen">
        <form className="auth-panel" onSubmit={submitLogin}>
          <div className="brand auth-brand">
            <div className="brand-mark">A</div>
            <div>
              <strong>A股短线工作台</strong>
              <span>请输入访问密码</span>
            </div>
          </div>
          <input
            autoFocus
            className="auth-input"
            type="password"
            value={loginPassword}
            onChange={(event) => setLoginPassword(event.target.value)}
            placeholder="访问密码"
          />
          {loginError && <div className="auth-error">{loginError}</div>}
          <button className="primary auth-submit" type="submit" disabled={loginLoading || !loginPassword}>
            {loginLoading ? <Loader2 size={16} className="spin" /> : <Play size={16} />}
            进入系统
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <strong>A股短线工作台</strong>
            <span>本地单人研究 · 预设驱动</span>
          </div>
        </div>

        <nav className="nav-list">
          <NavButton active={section === "desk"} icon={<Play size={16} />} label="运行台" onClick={() => setSection("desk")} />
          <NavButton active={section === "pool"} icon={<Layers3 size={16} />} label="机会池" onClick={() => setSection("pool")} />
          <NavButton active={section === "runs"} icon={<History size={16} />} label="运行记录" onClick={() => setSection("runs")} />
          <NavButton active={section === "automation"} icon={<Clock3 size={16} />} label="自动任务" onClick={() => setSection("automation")} />
          <NavButton active={section === "skills"} icon={<BrainCircuit size={16} />} label="Skill 工作室" onClick={() => setSection("skills")} />
          <NavButton active={section === "sources"} icon={<DatabaseZap size={16} />} label="数据源" onClick={() => setSection("sources")} />
          <NavButton active={section === "localPool"} icon={<HardDrive size={16} />} label="本地数据池" onClick={() => setSection("localPool")} />
          <NavButton active={section === "stats"} icon={<BarChart3 size={16} />} label="统计实验室" onClick={() => setSection("stats")} />
          <NavButton active={section === "backtest"} icon={<History size={16} />} label="回测" onClick={() => setSection("backtest")} />
        </nav>

        <div className="sidebar-card">
          <div className="sidebar-card-title">最近状态</div>
          <div className="metric-list">
            <div>
              <span>正式最近 Run</span>
              <strong>{bootstrap?.recent_runs.formal?.recommended_count ?? 0}</strong>
            </div>
            <div>
              <span>实验最近 Run</span>
              <strong>{bootstrap?.recent_runs.experiment?.recommended_count ?? 0}</strong>
            </div>
            <div>
              <span>启用 Skill</span>
              <strong>{skills.filter((skill) => skill.mode !== "disabled").length}</strong>
            </div>
          </div>
        </div>

      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <h1>{titleFor(section)}</h1>
            <p>{subtitleFor(section)}</p>
          </div>
          <div className="topbar-actions">
            <button onClick={() => void bootstrapApp()}>
              <RefreshCcw size={14} />
              刷新
            </button>
          </div>
        </header>

        {(error || message) && (
          <div className={`toast ${error ? "error" : "ok"}`}>
            {error ? <CircleAlert size={14} /> : <CheckCircle2 size={14} />}
            <span>{error || message}</span>
          </div>
        )}

        {section === "desk" && (
          <div className="desk-grid">
            <section className="panel">
              <PanelTitle title="运行配置" subtitle={editingPresetId ? "正在编辑当前预设" : "新建预设"} />
              <SkillConfigPanel
                runTarget={runTarget}
                setRunTarget={setRunTarget}
                selectedPresetId={selectedPresetId}
                setSelectedPresetId={setSelectedPresetId}
                presets={presets}
                skills={skills}
                editingPresetId={editingPresetId}
                presetDraft={presetDraft}
                onPresetDraftChange={(patch) => setPresetDraft((current) => ({ ...current, ...patch }))}
                presetSkillDrafts={presetSkillDrafts}
                onTogglePresetSkill={togglePresetSkill}
                onUpdatePresetSkill={updatePresetSkill}
                bucketLimits={bucketLimits}
                onBucketLimitChange={(bucket, value) => setBucketLimits((c) => ({ ...c, [bucket]: value }))}
                singleSkillId={singleSkillId}
                setSingleSkillId={setSingleSkillId}
                singleSkillWeight={singleSkillWeight}
                setSingleSkillWeight={setSingleSkillWeight}
                onSavePreset={() => void savePreset()}
                onResetToNewPreset={resetToNewPreset}
                onDuplicatePreset={duplicatePreset}
                onCopyPreset={duplicatePreset}
                onDeletePreset={() => void deletePreset()}
              />
            </section>

            <section className="panel">
              <PanelTitle title="当前摘要" subtitle={selectedPreset?.name ?? "未选择预设"} />
              {selectedPreset ? (
                <PresetSummary preset={selectedPreset} />
              ) : (
                <EmptyHint text="先选择一个预设" />
              )}

              <div className="divider" />

              <PanelTitle title="市场快照" subtitle={bootstrap?.market_snapshot.trade_date ?? ""} />
              {bootstrap?.market_snapshot ? (
                <MarketSnapshotPanel snapshot={bootstrap.market_snapshot} />
              ) : (
                <EmptyHint text="暂无市场快照" />
              )}

              <div className="run-hero">
                <button className="primary run-btn" onClick={() => void runCurrentSelection()} disabled={running}>
                  {running ? <Loader2 size={18} className="spin" /> : <Play size={18} />}
                  运行
                </button>
              </div>
            </section>
          </div>
        )}

        {section === "pool" && (
          <div className="pool-workspace">
            <section className="panel filter-panel pool-filter-bar">
              <PanelTitle
                title="筛选"
                subtitle="Run / 桶 / Skill"
                action={
                  <button className="ghost-btn subtle" onClick={() => setPoolFilters(EMPTY_POOL_FILTERS)}>
                    重置
                  </button>
                }
              />
              <div className="form-grid compact-grid">
                <SelectField
                  label="模式"
                  value={poolMode}
                  options={[
                    { value: "formal", label: "正式" },
                    { value: "experiment", label: "实验" },
                  ]}
                  onChange={(value) => setPoolMode(value as RunMode)}
                />
                <SelectField
                  label="Run"
                  value={selectedRunId || ALL_POOL_RUNS_VALUE}
                  options={poolRunOptions}
                  onChange={setSelectedRunId}
                />
                <SelectField
                  label="桶"
                  value={poolFilters.bucket}
                  options={[{ value: "all", label: "全部" }, ...availableBuckets.map((item) => ({ value: item, label: item }))]}
                  onChange={(value) => setPoolFilters((current) => ({ ...current, bucket: value }))}
                />
                <SelectField
                  label="行业"
                  value={poolFilters.industry}
                  options={[{ value: "all", label: "全部" }, ...availableIndustries.map((item) => ({ value: item, label: item }))]}
                  onChange={(value) => setPoolFilters((current) => ({ ...current, industry: value }))}
                />
                <SelectField
                  label="Skill"
                  value={poolFilters.skillId}
                  options={[{ value: "all", label: "全部" }, ...skills.map((item) => ({ value: item.skill_id, label: item.name }))]}
                  onChange={(value) => setPoolFilters((current) => ({ ...current, skillId: value }))}
                />
                <ToggleField
                  label="仅观察中"
                  value={poolFilters.watchOnly}
                  onChange={(value) => setPoolFilters((current) => ({ ...current, watchOnly: value }))}
                />
                <ToggleField
                  label="隐藏忽略票"
                  value={poolFilters.hideIgnored}
                  onChange={(value) => setPoolFilters((current) => ({ ...current, hideIgnored: value }))}
                />
                <ToggleField
                  label="隐藏重复票"
                  value={poolFilters.hideRepeat}
                  onChange={(value) => setPoolFilters((current) => ({ ...current, hideRepeat: value }))}
                />
              </div>
              <div className="mini-stat-row">
                <MiniStat label="当前推荐" value={filteredRecommendations.length} />
                <MiniStat label="容量核心" value={filteredRecommendations.filter((item) => item.bucket === "容量核心").length} />
                <MiniStat label="观察中" value={filteredRecommendations.filter((item) => item.watch_status === "watching").length} />
              </div>
            </section>

            <section className="panel pool-table-panel">
              <PanelTitle title="机会池" subtitle={poolTitleSubtitle} />
              <RecommendationList
                recommendations={filteredRecommendations}
                selectedId={selectedRecommendationId}
                onSelect={(id) => {
                  setSelectedRecommendationId(id);
                  setDetailModalOpen(true);
                }}
                onWatch={watchById}
                onDelete={(id) => void deleteRecommendation(id)}
              />
            </section>

            {detailModalOpen ? (
              <div className="modal-backdrop" onClick={() => setDetailModalOpen(false)}>
                <section className="detail-modal" role="dialog" aria-modal="true" aria-label={selectedRecommendation ? `${selectedRecommendation.name} 详情` : "股票详情"} onClick={(event) => event.stopPropagation()}>
                  <button className="modal-close" aria-label="关闭详情" onClick={() => setDetailModalOpen(false)}>
                    <X size={18} />
                  </button>
                  {selectedRecommendation ? (
                    <RecommendationPane
                      item={selectedRecommendation}
                      reviewDraft={reviewDraft}
                      outcomeDraft={outcomeDraft}
                      onReviewDraftChange={setReviewDraft}
                      onOutcomeDraftChange={setOutcomeDraft}
                      onWatch={updateWatchStatus}
                      onSaveReview={saveReview}
                      onSaveOutcome={saveOutcome}
                    />
                  ) : (
                    <EmptyHint text="详情加载中" />
                  )}
                </section>
              </div>
            ) : null}
          </div>
        )}

        {section === "runs" && (
          <RunHistoryPanel
            runs={runs}
            runHistoryMode={runHistoryMode}
            onModeChange={setRunHistoryMode}
            selectedRunId={selectedRunId}
            onSelect={(run) => {
              setPoolMode(run.run_mode);
              setSelectedRunId(run.run_id);
            }}
            onOpenPool={() => {
              setSection("pool");
            }}
            onDelete={(id) => void deleteRun(id)}
          />
        )}

        {section === "automation" && (
          <AutomationPage
            tasks={scheduledTasks}
            presets={presets}
            draft={taskDraft}
            editingTaskId={editingTaskId}
            onDraftChange={(patch) => setTaskDraft((current) => ({ ...current, ...patch }))}
            onEdit={editScheduledTask}
            onNew={resetScheduledTaskDraft}
            onSave={() => void saveScheduledTask()}
            onToggle={(task) => void toggleScheduledTask(task)}
            onRunNow={(task) => void runScheduledTaskNow(task)}
            onDelete={(taskId) => void deleteScheduledTask(taskId)}
            onRefresh={() => void loadScheduledTasks()}
          />
        )}

        {section === "skills" && (
          <div className="two-panel-grid">
            <section className="panel">
              <PanelTitle title="Skill 列表" subtitle="策略资产，而不是默认正式池" />
              <div className="install-box">
                <Field label="来源" value={installSource} onChange={setInstallSource} />
                <Field label="本地 ID" value={installSkillId} onChange={setInstallSkillId} />
                <button className="ghost-btn" onClick={() => void installSkill()}>
                  <Plus size={14} />
                  安装 Skill
                </button>
              </div>
              <div className="item-list">
                {skills.map((skill) => (
                  <button
                    key={skill.skill_id}
                    className={`list-item ${skill.skill_id === selectedSkillId ? "active" : ""}`}
                    onClick={() => setSelectedSkillId(skill.skill_id)}
                  >
                    <div>
                      <strong>{skill.name}</strong>
                      <span>
                        {displayText(skill.mode)} · {skill.current_version} · {skill.has_agent_contract ? "SKILL.md" : "仅规则"}
                      </span>
                    </div>
                    <div className="tag-stack">
                      <Tag text={skill.mode} />
                      {skill.has_agent_contract ? <span className="mini-chip">Agent</span> : null}
                    </div>
                  </button>
                ))}
              </div>
            </section>
            <section className="panel">
              {skillDetail ? (
                <SkillPane skill={skillDetail} onModeChange={setSkillMode} onBacktest={backtestSkill} />
              ) : (
                <EmptyHint text="点一个 Skill 看详情" />
              )}
            </section>
          </div>
        )}

        {section === "sources" && (
          <DataSourcePanel rows={dataSources} onRefresh={loadDataSources} onToggle={toggleDataSource} />
        )}

        {section === "localPool" && (
          <LocalPoolPage pool={localPool} onRefresh={loadLocalPool} onRefreshRemote={refreshLocalPool} refreshing={poolRefreshing} />
        )}

        {section === "stats" && (
          <div className="stats-grid">
            <section className="panel">
              <PanelTitle title="统计概览" subtitle={`当前模式：${displayText(statsMode)}`} />
              <div className="mode-switch">
                <button className={statsMode === "formal" ? "active" : ""} onClick={() => setStatsMode("formal")}>
                  <Target size={14} />
                  正式
                </button>
                <button className={statsMode === "experiment" ? "active" : ""} onClick={() => setStatsMode("experiment")}>
                  <Flame size={14} />
                  实验
                </button>
                <button className="ghost-btn" style={{ marginLeft: 16 }} onClick={() => void autoLabelPending()}>
                  <RefreshCcw size={14} />
                  批量回填
                </button>
              </div>
              {overview ? <OverviewCards overview={overview} /> : <EmptyHint text="暂无统计" />}
            </section>
            <section className="panel">
              <PanelTitle title="按 Skill" subtitle="触发率 / 胜率 / 平均收益" />
              <StatsTable rows={statsBySkill} nameKey="name" subKey="skill_id" countKey="sample_count" />
            </section>
            <section className="panel">
              <PanelTitle title="按预设" subtitle="正式与实验严格分开" />
              <StatsTable rows={statsByPreset} nameKey="preset_name" subKey="preset_id" countKey="recommendation_count" />
            </section>
          </div>
        )}
        {section === "backtest" && <BacktestPanel />}
      </main>
    </div>
  );
}

function titleFor(section: Section) {
  return {
    desk: "运行台",
    pool: "机会池",
    runs: "运行记录",
    automation: "自动任务",
    skills: "Skill 工作室",
    sources: "数据源",
    localPool: "本地数据池",
    stats: "统计实验室",
    backtest: "回测",
  }[section];
}

function subtitleFor(section: Section) {
  return {
    desk: "用预设组织一次运行，输出进入机会池。",
    pool: "按 Run、桶和 Skill 快速定位值得复盘的标的。",
    runs: "回看每次运行的市场状态、命中率和输出质量。",
    automation: "让系统按用户选择的时间自动刷新数据或触发策略预设。",
    skills: "管理策略资产，控制正式、实验和停用状态。",
    sources: "维护报价与日 K 数据源的健康度和参与状态。",
    localPool: "查看本地行情池覆盖情况，按需显式刷新外部源。",
    stats: "用可复盘的数据决定 Skill 晋级或淘汰。",
    backtest: "在历史日期重跑策略，自动标注 N 日胜率和收益。",
  }[section];
}

function messageFromError(err: unknown) {
  if (err instanceof Error) return err.message.replace(/^Error:\s*/, "");
  return String(err);
}


function NavButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className={`nav-btn ${active ? "active" : ""}`} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}

function PanelTitle({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="panel-title">
      <div>
        <h2>{title}</h2>
        {subtitle ? <span>{subtitle}</span> : null}
      </div>
      {action}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  multiline = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {multiline ? (
        <textarea value={value} onChange={(event) => onChange(event.target.value)} />
      ) : (
        <input value={value} onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function NumberField({
  label,
  value,
  onChange,
  step = 1,
  min,
  max,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  step?: number;
  min?: number;
  max?: number;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={(event) => onChange(Number(event.target.value) || 0)}
      />
    </label>
  );
}

function ToggleField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="toggle-field">
      <span>{label}</span>
      <button type="button" className={`toggle ${value ? "on" : "off"}`} onClick={() => onChange(!value)}>
        <span />
      </button>
    </div>
  );
}

function Tag({ text }: { text: string }) {
  return (
    <span className="tag" data-tone={toneForTag(text)}>
      {displayTagText(text)}
    </span>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="empty-hint">
      <EyeOff size={16} />
      <span>{text}</span>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PresetSummary({ preset }: { preset: Preset }) {
  return (
    <div className="summary-grid">
      <MiniStat label="模式" value={displayText(preset.run_mode)} />
      <MiniStat label="输出" value={preset.output_count} />
      <MiniStat label="风控" value={displayText(preset.risk_template)} />
      <MiniStat label="范围" value={displayText(preset.market_scope)} />
      <MiniStat label="重复票" value={preset.hide_recent_repeats ? "隐藏" : "展示"} />
      <div className="summary-block wide">
        <span>Skill</span>
        <div className="pill-row">
          {preset.skills.map((skill) => (
            <span key={skill.skill_id} className="pill static">
              {skill.name} x {skill.weight.toFixed(2)}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function AutomationPage({
  tasks,
  presets,
  draft,
  editingTaskId,
  onDraftChange,
  onEdit,
  onNew,
  onSave,
  onToggle,
  onRunNow,
  onDelete,
  onRefresh,
}: {
  tasks: ScheduledTask[];
  presets: Preset[];
  draft: ScheduledTaskDraft;
  editingTaskId: string | null;
  onDraftChange: (patch: Partial<ScheduledTaskDraft>) => void;
  onEdit: (task: ScheduledTask) => void;
  onNew: () => void;
  onSave: () => void;
  onToggle: (task: ScheduledTask) => void;
  onRunNow: (task: ScheduledTask) => void;
  onDelete: (taskId: string) => void;
  onRefresh: () => void;
}) {
  const recommendationTasks = tasks.filter((task) => task.task_type === "recommendation_run").length;
  const enabledTasks = tasks.filter((task) => task.enabled).length;
  const currentTask = tasks.find((task) => task.task_id === editingTaskId) ?? null;
  const runTimes = tasks
    .map((task) => task.last_run_at)
    .filter(Boolean)
    .sort();
  const latestRunAt = runTimes.length ? runTimes[runTimes.length - 1] : null;

  return (
    <div className="automation-grid">
      <section className="panel">
        <PanelTitle
          title="任务列表"
          subtitle="每日触发，按北京时间执行"
          action={
            <div className="inline-actions compact-actions">
              <button className="ghost-btn subtle" onClick={onRefresh}>
                <RefreshCcw size={14} />
                刷新
              </button>
              <button className="ghost-btn" onClick={onNew}>
                <Plus size={14} />
                新建
              </button>
            </div>
          }
        />
        <div className="summary-grid automation-summary">
          <MiniStat label="任务数" value={tasks.length} />
          <MiniStat label="启用中" value={enabledTasks} />
          <MiniStat label="推荐任务" value={recommendationTasks} />
          <MiniStat label="最近运行" value={latestRunAt ? formatShortTime(latestRunAt) : "-"} />
        </div>
        <div className="automation-task-list">
          {tasks.length ? (
            tasks.map((task) => (
              <button
                key={task.task_id}
                className={`automation-task ${task.task_id === editingTaskId ? "active" : ""}`}
                onClick={() => onEdit(task)}
              >
                <div className="automation-task-main">
                  <div>
                    <strong>{task.name}</strong>
                    <span>
                      {formatTaskTime(task)} · {displayText(task.task_type)}
                    </span>
                  </div>
                  <Tag text={task.enabled ? "active" : "inactive"} />
                </div>
                <div className="automation-task-meta">
                  <span>{describeTaskPayload(task, presets)}</span>
                  <span>{task.last_run_at ? `上次 ${formatShortTime(task.last_run_at)}` : "尚未运行"}</span>
                  {task.last_error ? <span className="danger-text">{task.last_error}</span> : null}
                </div>
              </button>
            ))
          ) : (
            <EmptyHint text="暂无自动任务" />
          )}
        </div>
      </section>

      <section className="panel">
        <PanelTitle title={editingTaskId ? "编辑任务" : "新建任务"} subtitle="选择时间和要触发的动作" />
        <div className="form-grid automation-form">
          <Field label="任务名称" value={draft.name} onChange={(value) => onDraftChange({ name: value })} />
          <label className="field">
            <span>触发时间</span>
            <input
              type="time"
              value={draft.trigger_time}
              onChange={(event) => onDraftChange({ trigger_time: event.target.value })}
            />
          </label>
          <SelectField
            label="任务类型"
            value={draft.task_type}
            options={[
              { value: "recommendation_run", label: "股票推荐" },
              { value: "pool_refresh", label: "更新本地股票池" },
              { value: "data_backfill", label: "数据回填" },
            ]}
            onChange={(value) => onDraftChange({ task_type: value as ScheduledTaskType })}
          />
          {draft.task_type === "recommendation_run" ? (
            <SelectField
              label="策略预设"
              value={draft.preset_id}
              options={[
                { value: "", label: "请选择预设" },
                ...presets.map((preset) => ({
                  value: preset.preset_id,
                  label: `${preset.name} · ${displayText(preset.run_mode)}`,
                })),
              ]}
              onChange={(value) => onDraftChange({ preset_id: value })}
            />
          ) : draft.task_type === "pool_refresh" ? (
            <div className="automation-note">
              <DatabaseZap size={16} />
              <span>到点后拉取外部行情并刷新本地股票池。</span>
            </div>
          ) : (
            <div className="automation-note">
              <DatabaseZap size={16} />
              <span>保留已有回填脚本参数，仅调整时间和启停状态。</span>
            </div>
          )}
          <ToggleField label="启用任务" value={draft.enabled} onChange={(value) => onDraftChange({ enabled: value })} />
        </div>
        <div className="inline-actions wrap">
          <button className="ghost-btn primary" onClick={onSave}>
            <CheckCircle2 size={14} />
            保存任务
          </button>
          <button className="ghost-btn subtle" onClick={onNew}>
            <Plus size={14} />
            新建
          </button>
        </div>

        {editingTaskId ? (
          <>
            <div className="divider" />
            <PanelTitle title="当前任务操作" subtitle={editingTaskId} />
            <div className="inline-actions wrap">
              {currentTask ? (
                <>
                  <button className="ghost-btn" onClick={() => onRunNow(currentTask)}>
                    <Play size={14} />
                    立即执行
                  </button>
                  <button className="ghost-btn" onClick={() => onToggle(currentTask)}>
                    <PauseCircle size={14} />
                    {currentTask.enabled ? "停用" : "启用"}
                  </button>
                  <button className="ghost-btn danger" onClick={() => onDelete(editingTaskId)}>
                    <Trash2 size={14} />
                    删除
                  </button>
                </>
              ) : null}
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}

function MarketContextBar({ market }: { market: MarketSnapshot | null }) {
  if (!market || !market.sample_count) return null;
  const breadthPct = market.sample_count > 0 ? ((market.up_count / market.sample_count) * 100).toFixed(0) : "0";
  const mainLines = (market.main_industries || []).slice(0, 4);
  return (
    <div className="market-context-bar">
      <span className="mc-stat">
        <span className="mc-label">市场</span>
        样本{market.sample_count}
      </span>
      <span className="mc-stat up">
        <ArrowUp size={12} />{market.up_count}
      </span>
      <span className="mc-stat down">
        <ArrowDown size={12} />{market.down_count}
      </span>
      <span className="mc-stat">
        <span className="mc-label">涨比</span>
        {breadthPct}%
      </span>
      {market.limit_up_count > 0 ? <span className="mc-stat limit-up">涨停{market.limit_up_count}</span> : null}
      {market.limit_down_count > 0 ? <span className="mc-stat limit-down">跌停{market.limit_down_count}</span> : null}
      {mainLines.length > 0 ? (
        <span className="mc-themes">
          主线：{mainLines.join("、")}
        </span>
      ) : null}
    </div>
  );
}

function actionTier(bucket: string, buySignal?: string, score?: number): { tier: string; cls: string; desc: string } {
  if (bucket === "容量核心" && (score ?? 0) >= 60) {
    return { tier: "A", cls: "tier-core", desc: "核心推荐 — 可执行，等买点触发" };
  }
  if (buySignal === "强烈买入" || buySignal === "买入") {
    return { tier: "B", cls: "tier-actionable", desc: "可执行 — 等买点触发" };
  }
  if (bucket === "明确回避" || (score ?? 100) < 45) {
    return { tier: "D", cls: "tier-avoid", desc: "回避 — 风险大于机会" };
  }
  return { tier: "C", cls: "tier-wait", desc: "等待触发 — 条件未满足" };
}

function PriceLevelGrid({ fs }: { fs: Record<string, unknown> }) {
  const levels = [
    { label: "MA5", value: fs.ma5, bias: fs.bias_ma5, support: fs.support_ma5 },
    { label: "MA10", value: fs.ma10, bias: fs.bias_ma10, support: fs.support_ma10 },
    { label: "MA20", value: fs.ma20, bias: fs.bias_ma20, support: undefined },
    { label: "MA60", value: fs.ma60, bias: undefined, support: undefined },
  ].filter((l) => l.value != null);

  if (!levels.length) return null;

  return (
    <div className="price-level-grid">
      {levels.map((l) => (
        <div key={l.label} className={`pl-cell ${l.support === true ? "supported" : l.support === false ? "unsupported" : ""}`}>
          <span className="pl-label">{l.label}</span>
          <strong className="pl-value">{Number(l.value).toFixed(2)}</strong>
          {l.bias != null ? (
            <span className={`pl-bias ${Number(l.bias) >= 0 ? "positive" : "negative"}`}>
              {Number(l.bias) >= 0 ? "+" : ""}{Number(l.bias).toFixed(1)}%
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function LevelLine({ label, value, unit }: { label: string; value: unknown; unit?: string }) {
  if (value == null || value === "") return null;
  const num = Number(value);
  const display = Number.isNaN(num) ? String(value) : num.toFixed(2);
  return (
    <span className="level-line">
      <span className="ll-label">{label}</span>
      <strong className="ll-value">{display}{unit ?? ""}</strong>
    </span>
  );
}

function CollapsibleSection({
  title,
  summary,
  defaultOpen = false,
  children,
}: {
  title: string;
  summary?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="detail-block collapsible">
      <button className="collapse-trigger" onClick={() => setOpen(!open)} aria-expanded={open}>
        <div className="collapse-header">
          <h3>{title}</h3>
          {summary ? <span className="collapse-summary">{summary}</span> : null}
        </div>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
      {open ? <div className="collapse-body">{children}</div> : null}
    </section>
  );
}

function RecommendationPane({
  item,
  reviewDraft,
  outcomeDraft,
  onReviewDraftChange,
  onOutcomeDraftChange,
  onWatch,
  onSaveReview,
  onSaveOutcome,
}: {
  item: RecommendationDetail;
  reviewDraft: ReviewDraft;
  outcomeDraft: OutcomeDraft;
  onReviewDraftChange: (value: ReviewDraft) => void;
  onOutcomeDraftChange: (value: OutcomeDraft) => void;
  onWatch: (status: string) => void;
  onSaveReview: () => void;
  onSaveOutcome: () => void;
}) {
  const fs = item.feature_snapshot;
  const tier = actionTier(item.bucket, String(fs.buy_signal ?? ""), item.final_score);
  const signalReasons: string[] = Array.isArray(fs.signal_reasons) ? fs.signal_reasons : [];
  const riskFactors: string[] = Array.isArray(fs.risk_factors) ? fs.risk_factors : [];
  const supportLevels: number[] = Array.isArray(fs.support_levels) ? fs.support_levels : [];
  const resistLevels: number[] = Array.isArray(fs.resistance_levels) ? fs.resistance_levels : [];
  const extraRisks: string[] = [];
  if (Number(fs.turnover_rate) >= 10) extraRisks.push(`换手率 ${Number(fs.turnover_rate).toFixed(1)}% 偏高`);
  if (Number(fs.upper_shadow_count20) >= 4) extraRisks.push(`连续上影线 ${fs.upper_shadow_count20} 天，抛压显现`);
  if (Number(fs.announcement_negative_count) > 0) extraRisks.push(`负面公告 ${fs.announcement_negative_count} 条`);
  if (Number(fs.news_risk_count) > 0) extraRisks.push(`风险舆情 ${fs.news_risk_count} 条`);
  if (Number(fs.max_dd20) < -15) extraRisks.push(`20日最大回撤 ${Number(fs.max_dd20).toFixed(1)}%`);

  return (
    <div className="detail-stack">
      <MarketContextBar market={item.market_context} />

      <header className="detail-hero">
        <div className="detail-identity">
          <span className="eyebrow">{item.code}</span>
          <h2>{item.name}</h2>
          <div className="detail-meta">
            <Tag text={item.bucket} />
            <span>{item.industry ?? "未分行业"}</span>
            {fs.suggested_position ? <span className="position-hint">{String(fs.suggested_position)}</span> : null}
          </div>
        </div>
        <div className="score-card">
          <span>模型评分</span>
          <strong>{item.final_score.toFixed(1)}</strong>
          <small>共识 {item.consensus_score.toFixed(0)}</small>
        </div>
      </header>

      <div className="detail-quick-actions">
        <button className="ghost-btn" onClick={() => onWatch("watching")}>
          <Eye size={14} />
          加入观察
        </button>
        <button className="ghost-btn subtle" onClick={() => onWatch("ignored")}>
          <X size={14} />
          忽略
        </button>
        <Tag text={item.watch_status} />
      </div>

      <section className={`operation-card ${tier.cls}`}>
        <div className="op-header">
          <div className="op-tier">
            <span className={`op-tier-badge ${tier.cls}`}>{tier.tier}</span>
            <div>
              <strong className="op-advice">{tier.desc}</strong>
              {fs.confidence_level ? (
                <span className="op-confidence">信心：{String(fs.confidence_level)}</span>
              ) : null}
            </div>
          </div>
          {fs.buy_signal ? (
            <span className={`buy-signal-pill ${String(fs.buy_signal)}`}>
              {String(fs.buy_signal)}
            </span>
          ) : null}
        </div>

        {(fs.ma5 != null || fs.ma10 != null || fs.ma20 != null) ? (
          <div className="op-section">
            <h4>关键价位</h4>
            <PriceLevelGrid fs={fs} />
            <div className="level-line-row">
              <LevelLine label="当前价" value={fs.price} />
              <LevelLine label="昨日高" value={fs.yesterday_high} />
              <LevelLine label="昨日低" value={fs.yesterday_low} />
              <LevelLine label="20日高" value={fs.high_20d} />
              <LevelLine label="20日低" value={fs.low_20d} />
            </div>
          </div>
        ) : null}

        <div className="op-split">
          <div className="op-section">
            <h4 className="op-buy-title">买入条件</h4>
            <ul className="op-condition-list buy">
              {signalReasons.length > 0
                ? signalReasons.slice(0, 4).map((r, i) => <li key={i}>{r}</li>)
                : <li>等待盘面确认信号</li>}
              {fs.ideal_buy != null ? (
                <li className="price-anchor">理想买点：<strong>{Number(fs.ideal_buy).toFixed(2)}</strong></li>
              ) : null}
              {fs.secondary_buy != null ? (
                <li className="price-anchor">次选买点：<strong>{Number(fs.secondary_buy).toFixed(2)}</strong></li>
              ) : null}
            </ul>
          </div>
          <div className="op-section">
            <h4 className="op-avoid-title">不买 / 止损</h4>
            <ul className="op-condition-list avoid">
              {riskFactors.length > 0
                ? riskFactors.slice(0, 4).map((r, i) => <li key={i}>{r}</li>)
                : <li>{item.invalid_rule || "跌破关键支撑不买"}</li>}
              {fs.stop_loss != null ? (
                <li className="price-anchor">止损线：<strong className="danger">{Number(fs.stop_loss).toFixed(2)}</strong></li>
              ) : null}
              {fs.take_profit != null ? (
                <li className="price-anchor">止盈线：<strong className="positive">{Number(fs.take_profit).toFixed(2)}</strong></li>
              ) : null}
            </ul>
          </div>
        </div>

        {(item.risk_tags.length > 0 || extraRisks.length > 0) ? (
          <div className="op-section">
            <h4 className="op-risk-title">风险提示</h4>
            <div className="risk-chips">
              {item.risk_tags.map((tag) => (
                <span key={tag} className="risk-chip danger">{tag}</span>
              ))}
              {extraRisks.map((r) => (
                <span key={r} className="risk-chip warning">{r}</span>
              ))}
            </div>
          </div>
        ) : null}

        {item.rationale ? (
          <div className="op-rationale">
            <BarChart3 size={14} />
            <span>{item.rationale}</span>
          </div>
        ) : null}
      </section>

      {(fs.trend_status || fs.macd_status || fs.rsi_12 != null) ? (
        <section className="detail-block tech-summary">
          <h3>技术指标</h3>
          <div className="tech-grid">
            {fs.trend_status ? <MiniStat label="趋势" value={String(fs.trend_status)} /> : null}
            {fs.ma_alignment ? <MiniStat label="均线排列" value={String(fs.ma_alignment)} /> : null}
            {fs.volume_status ? <MiniStat label="量能" value={String(fs.volume_status)} /> : null}
            {fs.volume_ratio_5d != null ? <MiniStat label="量比(5/20)" value={Number(fs.volume_ratio_5d).toFixed(2)} /> : null}
            {fs.macd_status ? <MiniStat label="MACD" value={String(fs.macd_status)} /> : null}
            {fs.rsi_12 != null ? <MiniStat label="RSI(12)" value={Number(fs.rsi_12).toFixed(1)} /> : null}
            {fs.breakout20 != null ? <MiniStat label="突破20日" value={Number(fs.breakout20) ? "是" : "否"} /> : null}
            {fs.off_high_pct != null ? <MiniStat label="距20日高" value={`${Number(fs.off_high_pct).toFixed(1)}%`} /> : null}
            {fs.trend_strength != null ? <MiniStat label="趋势强度" value={Number(fs.trend_strength).toFixed(1)} /> : null}
          </div>
        </section>
      ) : null}

      <CollapsibleSection
        title="Skill 贡献"
        summary={`${item.skill_breakdown.length} 个 Skill 参与评分`}
        defaultOpen={false}
      >
        <div className="signal-list">
          {item.skill_breakdown.map((signal) => (
            <div key={signal.version_id} className="signal-card">
              <div className="signal-head">
                <strong>{signal.strategy_name}</strong>
                <span>
                  {signal.score.toFixed(1)} · {signal.phase}
                </span>
              </div>
              <p>{signal.evidence.slice(0, 4).join("；") || "暂无证据"}</p>
              {signal.agent_skill_review ? (
                <div className="agent-skill-box">
                  <div>
                    <span>Agent Skill · {String(signal.agent_skill_review.runtime ?? signal.runtime ?? "local")}</span>
                    <strong>{String(signal.agent_skill_review.stance ?? "neutral")}</strong>
                  </div>
                  <p>{String(signal.agent_skill_review.summary ?? "")}</p>
                  <div className="pill-row">
                    {asStringArray(signal.agent_skill_review.lenses).slice(0, 4).map((lens) => (
                      <span className="pill static" key={lens}>{lens}</span>
                    ))}
                  </div>
                  <div className="agent-contract-grid">
                    <InfoLine label="动作" value={String(signal.agent_skill_review.action ?? "-")} />
                    <InfoLine label="仓位" value={String(signal.agent_skill_review.position_range ?? "-")} />
                    <InfoLine label="入场" value={String(signal.agent_skill_review.entry_style ?? "-")} />
                    <InfoLine label="市场" value={String(signal.agent_skill_review.market_regime_judgment ?? "-")} />
                    <InfoLine label="分数修正" value={String(signal.agent_skill_review.score_delta ?? 0)} />
                    <InfoLine label="置信度" value={`${(Number(signal.agent_skill_review.confidence ?? 0) * 100).toFixed(0)}%`} />
                    <InfoLine
                      label="Skill Hash"
                      value={String(signal.agent_skill_review.instruction_digest?.body_hash ?? signal.agent_skill_review.skill_hash ?? "-").slice(0, 12)}
                    />
                  </div>
                  {asStringArray(signal.agent_skill_review.invalidation_conditions).length ? (
                    <p>失效条件：{asStringArray(signal.agent_skill_review.invalidation_conditions).slice(0, 3).join("；")}</p>
                  ) : null}
                  {asStringArray(signal.agent_skill_review.next_day_plan).length ? (
                    <p>次日计划：{asStringArray(signal.agent_skill_review.next_day_plan).slice(0, 3).join("；")}</p>
                  ) : null}
                  {signal.agent_skill_review.runtime_note ? <p>{signal.agent_skill_review.runtime_note}</p> : null}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="数据证据" summary={String(fs.data_source ?? "-")} defaultOpen={false}>
        <div className="summary-grid">
          <MiniStat label="数据源" value={String(fs.data_source ?? "-")} />
          <MiniStat label="报价源" value={String(fs.quote_source ?? "-")} />
          <MiniStat label="日K源" value={String(fs.kline_source ?? "-")} />
          <MiniStat label="交易日" value={String(fs.latest_trade_date ?? item.trade_date)} />
          <MiniStat label="推荐价" value={item.recommend_price.toFixed(2)} />
          <MiniStat label="状态" value={displayText(item.status)} />
        </div>
        <div className="pill-row">
          {asStringArray((fs.data_quality as Record<string, unknown> | undefined)?.missing_fields).length ? (
            asStringArray((fs.data_quality as Record<string, unknown> | undefined)?.missing_fields).map((field) => (
              <span key={field} className="pill warning">
                缺 {field}
              </span>
            ))
          ) : (
            <span className="pill static">核心行情完整</span>
          )}
        </div>
      </CollapsibleSection>

      <section className="detail-block">
        <h3>复盘结果</h3>
        <InfoLine
          label="T+1/T+3/T+5"
          value={item.timeline.map((row) => `${row.label}:${row.close_return.toFixed(1)}%`).join(" / ") || "暂无"}
        />
        <InfoLine label="当前标签" value={item.outcome?.outcome_label || "pending"} />
        <div className="form-grid compact-grid">
          <SelectField
            label="标注"
            value={outcomeDraft.outcome_label}
            options={[
              { value: "pending", label: "待定" },
              { value: "success", label: "成功" },
              { value: "failed", label: "失败" },
              { value: "not_triggered", label: "未触发" },
              { value: "risk_hit", label: "风控触发" },
            ]}
            onChange={(value) => onOutcomeDraftChange({ ...outcomeDraft, outcome_label: value })}
          />
          <NumberField
            label="Horizon"
            value={outcomeDraft.horizon_days}
            min={1}
            max={20}
            onChange={(value) => onOutcomeDraftChange({ ...outcomeDraft, horizon_days: value })}
          />
          <Field
            label="标注原因"
            value={outcomeDraft.outcome_reason}
            onChange={(value) => onOutcomeDraftChange({ ...outcomeDraft, outcome_reason: value })}
          />
          <Field
            label="复盘笔记"
            value={reviewDraft.review_text}
            onChange={(value) => onReviewDraftChange({ ...reviewDraft, review_text: value })}
            multiline
          />
        </div>
        <div className="inline-actions wrap">
          <button className="ghost-btn" onClick={onSaveReview}>
            <NotebookPen size={14} />
            保存复盘
          </button>
          <button className="ghost-btn" onClick={onSaveOutcome}>
            <Target size={14} />
            标注结果
          </button>
        </div>
      </section>

      <section className="detail-block">
        <h3>历史推荐</h3>
        <div className="history-list">
          {item.history.length ? (
            item.history.map((row) => (
              <div key={row.recommendation_id} className="history-item">
                <strong>{row.trade_date}</strong>
                <span>
                  {row.bucket} · {row.final_score.toFixed(1)} · {displayText(row.status)}
                </span>
              </div>
            ))
          ) : (
            <EmptyHint text="暂无历史推荐记录" />
          )}
        </div>
      </section>
    </div>
  );
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-line">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function makeTaskId(draft: ScheduledTaskDraft) {
  const suffix = `${draft.task_type}_${draft.trigger_time || "time"}_${draft.preset_id || "pool"}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 52);
  return `task_${suffix}_${Date.now().toString(36)}`;
}

function taskToPayload(task: ScheduledTask, presets: Preset[]): ScheduledTaskPayload {
  const triggerTime = task.trigger_time ?? cronToClockTime(task.cron_expression) ?? "07:00";
  const presetId = String(task.payload?.preset_id ?? "");
  const payload: ScheduledTaskPayload = {
    task_id: task.task_id,
    name: task.name,
    task_type: task.task_type,
    trigger_time: triggerTime,
    payload: task.task_type === "recommendation_run" ? { ...task.payload, preset_id: presetId } : { ...task.payload },
    enabled: task.enabled,
  };
  if (!task.trigger_time && task.cron_expression && triggerTime === cronToClockTime(task.cron_expression)) {
    payload.cron_expression = task.cron_expression;
    delete payload.trigger_time;
  }
  return payload;
}

function formatTaskTime(task: ScheduledTask) {
  return task.trigger_time ?? cronToClockTime(task.cron_expression) ?? task.cron_expression;
}

function cronToClockTime(cronExpression: string) {
  const parts = cronExpression.split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour] = parts;
  const h = Number(hour);
  const m = Number(minute);
  if (!Number.isInteger(h) || !Number.isInteger(m)) return null;
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function describeTaskPayload(task: ScheduledTask, presets: Preset[]) {
  if (task.task_type === "pool_refresh") return "本地股票池";
  if (task.task_type === "recommendation_run") {
    const presetId = String(task.payload?.preset_id ?? "");
    return presets.find((preset) => preset.preset_id === presetId)?.name ?? presetId ?? "未选择预设";
  }
  return "数据回填";
}

function displayText(key: string): string {
  const labels: Record<string, string> = {
    recommendation_run: "股票推荐",
    pool_refresh: "更新本地股票池",
    data_backfill: "数据回填",
    formal: "正式",
    experiment: "实验",
    disabled: "停用",
    conservative: "保守",
    balanced: "均衡",
    aggressive: "激进",
    local_pool: "仅本地池",
    local_pool_preferred: "本地池优先",
    realtime: "仅实时数据源",
    all: "全部",
    watchlist: "自选股",
    industry: "行业",
    pending: "待定",
    success: "成功",
    failed: "失败",
    not_triggered: "未触发",
    risk_hit: "风控触发",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    interrupted: "已中断",
    stale: "疑似中断",
    prepare: "准备运行",
    starting: "启动中",
    market_data: "市场数据",
    scope_filter: "范围过滤",
    auxiliary_data: "辅助数据",
    feature_build: "特征构建",
    rule_scan: "规则扫描",
    skill_gate: "规则门",
    skill_review: "Skill 深审",
    aggregate: "聚合",
    persist: "保存结果",
    ready: "可运行",
    blocked: "不可运行",
    warning: "有缺口",
    healthy: "健康",
    degraded: "降级",
    untested: "未测试",
    watching: "观察中",
    ignored: "已忽略",
    none: "未观察",
    inactive: "停用",
  };
  return labels[key] ?? key;
}

function displayTagText(text: string) {
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    disabled: "停用",
    healthy: "健康",
    degraded: "降级",
    untested: "未测试",
    watching: "观察中",
    ignored: "已忽略",
    none: "未观察",
    pending: "待验证",
    success: "成功",
    failed: "失败",
    not_triggered: "未触发",
    risk_hit: "风控命中",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    interrupted: "已中断",
    stale: "疑似中断",
    local_pool: "仅本地池",
    local_pool_preferred: "本地池优先",
    realtime: "仅实时数据源",
    ready: "可运行",
    blocked: "不可运行",
    warning: "有缺口",
  };
  return displayText(text) || labels[text] || text;
}

function toneForTag(text: string) {
  if (["formal", "watching", "success", "容量核心", "ready"].includes(text)) return "positive";
  if (["healthy"].includes(text)) return "positive";
  if (["experiment", "pending", "高弹观察", "warning", "local_pool_preferred", "queued", "running"].includes(text)) return "warning";
  if (["degraded", "untested"].includes(text)) return "warning";
  if (["failed", "ignored", "risk_hit", "blocked", "interrupted", "stale"].includes(text)) return "danger";
  if (["not_triggered", "realtime"].includes(text)) return "blue";
  if (["disabled", "none", "inactive", "local_pool"].includes(text)) return "muted";
  return "neutral";
}

function SkillPane({
  skill,
  onModeChange,
  onBacktest,
}: {
  skill: SkillDetail;
  onModeChange: (mode: SkillMode) => void;
  onBacktest: () => void;
}) {
  return (
    <div className="detail-stack">
      <PanelTitle title={skill.name} subtitle={`${skill.skill_id} · ${skill.current_version}`} />
      <div className="summary-grid">
        <MiniStat label="状态" value={displayText(skill.mode)} />
        <MiniStat label="运行时" value={skill.agent_runtime || (skill.has_agent_contract ? "agent" : "rule")} />
        <MiniStat label="SKILL.md" value={skill.has_agent_contract ? "已接入" : "缺失"} />
        <MiniStat label="规则" value={skill.has_executable_contract ? "可回测" : "Agent-only"} />
        <MiniStat label="正式样本" value={skill.performance.formal.sample_count} />
        <MiniStat label="正式胜率" value={`${(skill.performance.formal.hit_rate * 100).toFixed(1)}%`} />
        <MiniStat
          label="实验胜率"
          value={`${(skill.performance.experiment.hit_rate * 100).toFixed(1)}%`}
        />
      </div>
      <section className="detail-block">
        <h3>Agent 接入状态</h3>
        <div className="summary-grid">
          <MiniStat label="指令源" value={skill.has_agent_contract ? "SKILL.md" : "无"} />
          <MiniStat label="References" value={skill.reference_files?.length ?? 0} />
          <MiniStat label="执行模式" value={skill.has_executable_contract ? "规则 + Agent" : "Agent-only"} />
        </div>
        {skill.reference_files?.length ? (
          <div className="reference-list">
            {skill.reference_files.slice(0, 8).map((ref) => (
              <div className="reference-row" key={ref.path}>
                <strong>{ref.path}</strong>
                <span>{ref.sha256?.slice(0, 12) ?? ""}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyHint text="这个 Skill 没有 references 目录，仍会使用 SKILL.md 运行" />
        )}
      </section>
      <div className="inline-actions wrap">
        <button className="ghost-btn" onClick={() => onModeChange("experiment")}>
          <Flame size={14} />
          设为实验
        </button>
        <button className="ghost-btn" onClick={() => onModeChange("formal")}>
          <Target size={14} />
          提升正式
        </button>
        <button className="ghost-btn" onClick={() => onModeChange("disabled")}>
          <PauseCircle size={14} />
          停用
        </button>
        <button className="ghost-btn" onClick={onBacktest}>
          <Wrench size={14} />
          回测
        </button>
      </div>
      <section className="detail-block">
        <h3>方法说明</h3>
        <pre>{skill.skill_md || "暂无说明"}</pre>
      </section>
      <section className="detail-block">
        <h3>失败原因</h3>
        <div className="history-list">
          {skill.performance.formal.failure_reasons.length ? (
            skill.performance.formal.failure_reasons.map((row) => (
              <div key={row.reason} className="history-item">
                <strong>{row.reason}</strong>
                <span>{row.count}</span>
              </div>
            ))
          ) : (
            <EmptyHint text="暂无失败原因统计" />
          )}
        </div>
      </section>
    </div>
  );
}

function LocalPoolPage({
  pool,
  onRefresh,
  onRefreshRemote,
  refreshing,
}: {
  pool: LocalPoolStatus | null;
  onRefresh: (params?: { u_limit?: number; u_offset?: number; b_limit?: number; b_offset?: number }) => void;
  onRefreshRemote: () => void;
  refreshing: boolean;
}) {
  const pageSize = 50;
  const [uPage, setUPage] = useState(1);
  const [bPage, setBPage] = useState(1);
  const [hoveredQuote, setHoveredQuote] = useState<{
    row: UnifiedQuotePoolRow;
    quote: Record<string, unknown>;
    rawSources: string[];
    x: number;
    y: number;
  } | null>(null);

  if (!pool) {
    return (
      <div className="sources-workspace">
        <section className="panel">
          <PanelTitle
            title="本地数据池"
            subtitle="等待加载"
            action={
              <div className="inline-actions">
                <button className="ghost-btn" onClick={() => onRefresh()}><RefreshCcw size={14} />刷新状态</button>
                <button className="ghost-btn primary" onClick={onRefreshRemote} disabled={refreshing}>
                  {refreshing ? <Loader2 size={14} className="spin" /> : <DatabaseZap size={14} />}
                  拉取外部源
                </button>
              </div>
            }
          />
          <div className="loading-block">
            <Loader2 size={32} className="spin" />
            <span>正在加载本地数据池…</span>
          </div>
        </section>
      </div>
    );
  }

  const uTotal = pool.unified_quotes_total ?? pool.unified_quotes.length;
  const bTotal = pool.daily_bars_total ?? pool.daily_bars.length;
  const uTotalPages = Math.max(1, Math.ceil(uTotal / pageSize));
  const bTotalPages = Math.max(1, Math.ceil(bTotal / pageSize));
  const readiness = pool.recommendation_readiness;
  const readinessTone = readiness.status === "ready" ? "ready" : readiness.status === "warning" ? "warning" : "blocked";
  const readinessTitle =
    readiness.status === "ready"
      ? "可进行本地推荐"
      : readiness.status === "warning"
        ? "本地推荐可运行，但数据不完整"
        : "本地池暂不能推荐股票";
  const readinessSubtitle =
    readiness.messages[0] ??
    (readiness.can_run_local_recommendation ? "本地池当前可运行。" : "请先补齐本地报价和日K覆盖。");
  const candidateFilter = pool.summary.candidate_filter ?? readiness.quote.filter_summary;
  const highLiquidityCount = candidateFilter?.high_liquidity_count;
  const lowLiquidityCount = candidateFilter?.low_liquidity_count;
  const excludedCount = candidateFilter?.excluded_count;

  function goUPage(page: number) {
    const p = Math.max(1, Math.min(page, uTotalPages));
    setUPage(p);
    onRefresh({ u_limit: pageSize, u_offset: (p - 1) * pageSize, b_limit: pageSize, b_offset: (bPage - 1) * pageSize });
  }

  function goBPage(page: number) {
    const p = Math.max(1, Math.min(page, bTotalPages));
    setBPage(p);
    onRefresh({ u_limit: pageSize, u_offset: (uPage - 1) * pageSize, b_limit: pageSize, b_offset: (p - 1) * pageSize });
  }

  function showQuoteTooltip(event: MouseEvent<HTMLDivElement>, row: UnifiedQuotePoolRow, quote: Record<string, unknown>, rawSources: string[]) {
    setHoveredQuote({ row, quote, rawSources, x: event.clientX, y: event.clientY });
  }

  function moveQuoteTooltip(event: MouseEvent<HTMLDivElement>) {
    setHoveredQuote((current) => current ? { ...current, x: event.clientX, y: event.clientY } : current);
  }

  return (
    <div className="sources-workspace">
      <section className="panel source-overview-panel">
        <PanelTitle
          title="本地数据池"
          subtitle="多源共同维护的本地行情底座"
          action={
            <div className="inline-actions">
              <button className="ghost-btn" onClick={() => onRefresh()}>
                <RefreshCcw size={14} />
                刷新状态
              </button>
              <button className="ghost-btn primary" onClick={onRefreshRemote} disabled={refreshing}>
                {refreshing ? <Loader2 size={14} className="spin" /> : <DatabaseZap size={14} />}
                拉取外部源
              </button>
            </div>
          }
        />
        <div className={`pool-readiness-card ${readinessTone}`}>
          <div className="pool-readiness-main">
            <div className="pool-readiness-icon">
              {readiness.status === "ready" ? <CheckCircle2 size={22} /> : <CircleAlert size={22} />}
            </div>
            <div>
              <h3>{readinessTitle}</h3>
              <p>{readinessSubtitle}</p>
              {readiness.messages.length > 1 && (
                <div className="pool-readiness-messages">
                  {readiness.messages.slice(1, 3).map((message) => (
                    <span key={message}>{message}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="pool-readiness-stats">
            <MiniStat label="统一报价" value={readiness.quote.unified_quote_total ?? readiness.quote.available_count} />
            <MiniStat label="基础合格" value={readiness.quote.base_filter_eligible_count ?? readiness.quote.candidate_count} />
            <MiniStat label="高流动性" value={highLiquidityCount ?? "-"} />
            <MiniStat label="低流动性" value={lowLiquidityCount ?? "-"} />
            <MiniStat label="目标候选" value={readiness.required_candidate_count} />
            <MiniStat label="本次扫描" value={readiness.scan?.actual_scan_count ?? readiness.kline.ready_count} />
            <MiniStat label="Skill 输入" value={readiness.scan?.skill_input_count ?? 0} />
            <MiniStat label="日K覆盖" value={readiness.kline.ready_count} />
            <MiniStat label="日K缺失" value={readiness.kline.missing_count} />
            <MiniStat label="短历史" value={readiness.kline.short_history_count ?? 0} />
            <MiniStat label="日K过期" value={readiness.kline.stale_count} />
            <MiniStat label="覆盖率" value={`${(((readiness.kline.coverage_rate ?? 0) as number) * 100).toFixed(1)}%`} />
          </div>
          {readiness.scan?.demo_limited ? (
            <div className="pool-readiness-messages prominent">
              <span>
                Demo 限流模式：
                {readiness.scan.configured_candidate_count > 0 ? "推荐扫描少于基础合格股票" : "Skill 输入少于实际扫描股票"}
                ，请确认配置后再做正式验收。
              </span>
            </div>
          ) : null}
          {readiness.scan && !readiness.scan.demo_limited ? (
            <div className="pool-readiness-messages">
              <span>
                全市场扫描：基础合格 {readiness.quote.base_filter_eligible_count ?? readiness.quote.candidate_count}
                {candidateFilter ? `，高流动性 ${candidateFilter.high_liquidity_count}，低流动性 ${candidateFilter.low_liquidity_count}` : ""}
                ，本次实际扫描 {readiness.scan.actual_scan_count}
                ，Skill 接收 {readiness.scan.skill_input_count}。
              </span>
            </div>
          ) : null}
          {readiness.kline.examples.length > 0 && (
            <div className="pool-readiness-examples">
              {readiness.kline.examples.slice(0, 6).map((item) => (
                <div className="readiness-example" key={`${item.code}-${item.reason}`}>
                  <strong>{item.name || item.code || "-"}</strong>
                  <span>{item.code || "-"} · {item.reason}</span>
                  <small>
                    日K {item.bar_count} 条 · 最新 {item.latest_bar_date || "-"} · 报价 {item.quote_date || "-"}
                  </small>
                </div>
              ))}
            </div>
          )}
          <div className="pool-readiness-actions">
            <button className="ghost-btn" onClick={() => onRefresh()}>
              <RefreshCcw size={14} />
              重新检测
            </button>
            <button className="ghost-btn primary" onClick={onRefreshRemote} disabled={refreshing}>
              {refreshing ? <Loader2 size={14} className="spin" /> : <DatabaseZap size={14} />}
              拉取外部源
            </button>
          </div>
        </div>
        <div className="summary-grid source-summary-grid">
          <MiniStat label="原始报价" value={pool.summary.raw_quote_count} />
          <MiniStat label="统一报价" value={pool.summary.active_unified_quote_count} />
          <MiniStat label="合格数" value={pool.summary.eligible_quote_count ?? readiness.quote.candidate_count} />
          <MiniStat label="高流动性" value={highLiquidityCount ?? "-"} />
          <MiniStat label="低流动性" value={lowLiquidityCount ?? "-"} />
          <MiniStat label="策略排除" value={excludedCount ?? "-"} />
          <MiniStat label="原始日K" value={pool.summary.raw_bar_count} />
          <MiniStat label="全市场日K" value={pool.summary.full_market_daily_bar_count ?? pool.summary.daily_bar_count} />
          <MiniStat label="全市场覆盖率" value={`${Number(pool.summary.daily_bar_coverage_rate ?? 0).toFixed(1)}%`} />
          <MiniStat label="合格日K" value={pool.summary.eligible_daily_bar_count ?? readiness.kline.ready_count} />
          <MiniStat label="合格覆盖率" value={`${Number(pool.summary.eligible_daily_bar_coverage_rate ?? ((readiness.kline.coverage_rate ?? 0) * 100)).toFixed(1)}%`} />
          <MiniStat label="日K缺失" value={pool.summary.daily_bar_missing_count ?? readiness.kline.missing_count} />
          <MiniStat label="短历史" value={pool.summary.daily_bar_short_history_count ?? 0} />
          <MiniStat label="日K过期" value={pool.summary.daily_bar_stale_count ?? readiness.kline.stale_count} />
          <MiniStat label="推荐扫描" value={pool.summary.actual_recommend_scan_count ?? readiness.kline.ready_count} />
          <MiniStat label="Skill 输入" value={pool.summary.skill_input_count ?? 0} />
          <MiniStat label="技术指标" value={pool.summary.daily_indicator_count} />
          <MiniStat label="指标覆盖率" value={pool.summary.indicator_coverage_pct != null ? `${pool.summary.indicator_coverage_pct}%` : "-"} />
          <MiniStat label="报价日期" value={pool.summary.latest_quote_date || "-"} />
          <MiniStat label="日K日期" value={pool.summary.latest_bar_date || "-"} />
          <MiniStat label="覆盖基准" value={pool.summary.coverage_trade_date || "-"} />
          <MiniStat label="更新时间" value={pool.summary.latest_unified_at ? formatShortTime(pool.summary.latest_unified_at) : "-"} />
          <MiniStat label="历史统一报价" value={pool.summary.unified_quote_count} />
        </div>
      </section>

      <section className="panel">
        <PanelTitle title="分源沉淀" subtitle="每个源写入本地池的覆盖量" />
        <div className="pool-source-grid">
          <PoolSourceGroup title="报价原始池" rows={pool.quote_sources} />
          <PoolSourceGroup title="日K原始池" rows={pool.kline_sources} />
        </div>
      </section>

      <section className="panel">
        <PanelTitle title="统一报价池" subtitle="当前参与推荐的合成报价样本 — 可横向滚动查看更多列" />
        <div className="pool-table-scroll">
          <div className="pool-table-list">
            <div className="pool-row pool-header">
              <div>代码/名称</div>
              <div>价格</div>
              <div>涨幅%</div>
              <div>MA5</div>
              <div>MA10</div>
              <div>MA20</div>
              <div>成交额(亿)</div>
              <div>换手%</div>
              <div>PE(TTM)</div>
              <div>市值(亿)</div>
              <div>质量分</div>
              <div>60日涨跌%</div>
              <div>年初涨跌%</div>
              <div>主力净流入</div>
              <div>来源</div>
            </div>
            {pool.unified_quotes.length ? (
              pool.unified_quotes.map((row) => {
                const quote = (row.quote || {}) as Record<string, unknown>;
                const rawSources = asStringArray(row.source_trace.raw_sources);
                return (
                  <div
                    className="pool-row"
                    key={row.code}
                    onMouseEnter={(event) => showQuoteTooltip(event, row, quote, rawSources)}
                    onMouseMove={moveQuoteTooltip}
                    onMouseLeave={() => setHoveredQuote(null)}
                  >
                    <div>
                      <strong>{String(quote.name ?? row.code)}</strong>
                      <span>{row.code}</span>
                    </div>
                    <div><strong>{formatNumber(quote.price)}</strong></div>
                    <div style={{ color: Number(quote.pct ?? 0) >= 0 ? 'var(--emerald)' : 'var(--danger)' }}>
                      <strong>{Number(quote.pct ?? 0) >= 0 ? '+' : ''}{formatNumber(quote.pct)}%</strong>
                    </div>
                    <div><strong>{quote.ma5 != null ? formatNumber(quote.ma5) : '-'}</strong></div>
                    <div><strong>{quote.ma10 != null ? formatNumber(quote.ma10) : '-'}</strong></div>
                    <div><strong>{quote.ma20 != null ? formatNumber(quote.ma20) : '-'}</strong></div>
                    <div><strong>{quote.amount_yi != null ? formatYi(quote.amount_yi) : '-'}</strong></div>
                    <div><strong>{quote.turnover_rate != null ? formatNumber(quote.turnover_rate) + '%' : '-'}</strong></div>
                    <div><strong>{quote.pe_ttm != null ? formatNumber(quote.pe_ttm) : '-'}</strong></div>
                    <div><strong>{quote.total_mv_yi != null ? formatYi(quote.total_mv_yi) : '-'}</strong></div>
                    <div><strong>{row.quality_score.toFixed(1)}</strong></div>
                    <div><strong>{quote.chg60 != null ? formatNumber(quote.chg60) + '%' : '-'}</strong></div>
                    <div><strong>{quote.ytd_chg != null ? formatNumber(quote.ytd_chg) + '%' : '-'}</strong></div>
                    <div><strong>{quote.main_net_inflow_yi != null ? formatYi(quote.main_net_inflow_yi) : '-'}</strong></div>
                    <div className="pool-row-tags">
                      <Tag text={row.freshness_status} />
                      {rawSources.slice(0, 2).map((source) => (
                        <span className="mini-chip" key={source}>{source}</span>
                      ))}
                    </div>
                  </div>
                );
              })
            ) : (
              <EmptyHint text="暂无统一报价" />
            )}
          </div>
        </div>
        {hoveredQuote && (
          <PoolTooltip
            row={hoveredQuote.row}
            quote={hoveredQuote.quote}
            rawSources={hoveredQuote.rawSources}
            x={hoveredQuote.x}
            y={hoveredQuote.y}
          />
        )}
        {uTotalPages > 1 && (
          <div className="pagination-bar">
            <button className="ghost-btn" disabled={uPage <= 1} onClick={() => goUPage(uPage - 1)}>上一页</button>
            <span>第 {uPage} / {uTotalPages} 页（共 {uTotal} 条）</span>
            <button className="ghost-btn" disabled={uPage >= uTotalPages} onClick={() => goUPage(uPage + 1)}>下一页</button>
          </div>
        )}
      </section>

      <section className="panel">
        <PanelTitle title="日K覆盖" subtitle="本地标准日 K 缓存覆盖样本" />
        <div className="bar-coverage-grid">
          {pool.daily_bars.length ? (
            pool.daily_bars.map((row) => (
              <div className="bar-coverage-card" key={row.code}>
                <strong>{row.code}</strong>
                <span>{row.first_trade_date} - {row.latest_trade_date}</span>
                <em>{row.bar_count} 条</em>
              </div>
            ))
          ) : (
            <EmptyHint text="暂无日K缓存" />
          )}
        </div>
        {bTotalPages > 1 && (
          <div className="pagination-bar">
            <button className="ghost-btn" disabled={bPage <= 1} onClick={() => goBPage(bPage - 1)}>上一页</button>
            <span>第 {bPage} / {bTotalPages} 页（共 {bTotal} 条）</span>
            <button className="ghost-btn" disabled={bPage >= bTotalPages} onClick={() => goBPage(bPage + 1)}>下一页</button>
          </div>
        )}
      </section>
    </div>
  );
}

function PoolSourceGroup({ title, rows }: { title: string; rows: Array<{ source: string; row_count: number; code_count: number; latest_trade_date?: string | null; latest_fetched_at?: string | null }> }) {
  return (
    <div className="pool-source-group">
      <h3>{title}</h3>
      {rows.length ? (
        rows.map((row) => (
          <div className="pool-source-row" key={row.source}>
            <div>
              <strong>{row.source}</strong>
              <span>{row.latest_trade_date || "-"}</span>
            </div>
            <div>
              <span>代码</span>
              <strong>{row.code_count}</strong>
            </div>
            <div>
              <span>记录</span>
              <strong>{row.row_count}</strong>
            </div>
          </div>
        ))
      ) : (
        <EmptyHint text="暂无分源记录" />
      )}
    </div>
  );
}

function formatYi(value: unknown) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number) || number === 0) return "-";
  const yi = Math.abs(number) > 1_000_000 ? number / 1e8 : number;
  return yi.toFixed(yi >= 10 ? 1 : 2);
}

function PoolTooltip({
  row,
  quote,
  rawSources,
  x,
  y,
}: {
  row: UnifiedQuotePoolRow;
  quote: Record<string, unknown>;
  rawSources: string[];
  x: number;
  y: number;
}) {
  const fields: { label: string; key: string; fmt?: (v: unknown) => string }[] = [
    { label: "价格", key: "price", fmt: (v) => formatNumber(v) },
    { label: "涨幅%", key: "pct", fmt: (v) => formatNumber(v) },
    { label: "MA5", key: "ma5", fmt: (v) => formatNumber(v) },
    { label: "MA10", key: "ma10", fmt: (v) => formatNumber(v) },
    { label: "MA20", key: "ma20", fmt: (v) => formatNumber(v) },
    { label: "成交额(亿)", key: "amount_yi", fmt: (v) => formatYi(v) },
    { label: "换手%", key: "turnover_rate", fmt: (v) => formatNumber(v) },
    { label: "PE(TTM)", key: "pe_ttm", fmt: (v) => formatNumber(v) },
    { label: "市值(亿)", key: "total_mv_yi", fmt: (v) => formatYi(v) },
    { label: "60日涨跌%", key: "chg60", fmt: (v) => formatNumber(v) },
    { label: "年初涨跌%", key: "ytd_chg", fmt: (v) => formatNumber(v) },
    { label: "主力净流入(亿)", key: "main_net_inflow_yi", fmt: (v) => formatYi(v) },
    { label: "成交量(手)", key: "volume", fmt: (v) => formatNumber(v) },
  ];

  const name = String(quote.name ?? row.code);
  const industry = String(quote.industry ?? "");
  const isST = quote.is_st != null ? String(quote.is_st) : "";

  const sourceTrace = (row.source_trace || {}) as Record<string, unknown>;
  const conflicts = asStringArray(sourceTrace.conflicts);
  const tooltipWidth = 380;
  const tooltipHeight = 560;
  const left = Math.min(x + 18, window.innerWidth - tooltipWidth - 16);
  const top = Math.min(y + 18, window.innerHeight - tooltipHeight - 16);

  return (
    <div
      className="pool-tooltip"
      style={{ left: Math.max(16, left), top: Math.max(16, top) }}
    >
      <h4>{name}</h4>

      <div className="pool-tooltip-section">
        <h5>基本信息</h5>
        <div className="pool-tooltip-grid">
          <div className="kv"><span className="label">代码</span><span className="value">{row.code}</span></div>
          <div className="kv"><span className="label">行业</span><span className="value">{industry || "-"}</span></div>
          <div className="kv"><span className="label">ST</span><span className="value">{isST || "否"}</span></div>
          <div className="kv"><span className="label">交易日期</span><span className="value">{row.trade_date}</span></div>
        </div>
      </div>

      <div className="pool-tooltip-section">
        <h5>行情数据</h5>
        <div className="pool-tooltip-grid">
          {fields.map((f) => {
            const raw = quote[f.key as string];
            const present = raw !== null && raw !== undefined;
            return (
              <div className="kv" key={f.key}>
                <span className="label">{f.label}</span>
                {present ? (
                  <span className="value">{f.fmt ? f.fmt(raw) : String(raw)}</span>
                ) : (
                  <span className="missing">缺失</span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="pool-tooltip-section">
        <h5>数据质量</h5>
        <div className="pool-tooltip-grid">
          <div className="kv"><span className="label">来源数</span><span className="value">{row.source_count}</span></div>
          <div className="kv"><span className="label">质量分</span><span className="value">{row.quality_score.toFixed(1)}</span></div>
          <div className="kv"><span className="label">新鲜度</span><span className="value">{row.freshness_status}</span></div>
          <div className="kv"><span className="label">更新时间</span><span className="value">{row.fetched_at ? formatShortTime(row.fetched_at) : "-"}</span></div>
        </div>
        {conflicts.length > 0 && (
          <div style={{ marginTop: 6, fontSize: 12, color: 'var(--gold)' }}>冲突: {conflicts.join(", ")}</div>
        )}
        {rawSources.length > 0 && (
          <div className="tooltip-sources" style={{ marginTop: 6 }}>
            {rawSources.map((s) => (
              <span className="mini-chip" key={s}>{s}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function formatNumber(value: unknown) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) return "-";
  return number.toFixed(Math.abs(number) >= 100 ? 1 : 2);
}

function formatShortTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1}-${date.getDate()} ${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}

function OverviewCards({ overview }: { overview: StatsOverview }) {
  return (
    <div className="summary-grid">
      <MiniStat label="运行数" value={overview.run_count} />
      <MiniStat label="推荐数" value={overview.recommendation_count} />
      <MiniStat label="已标注" value={overview.labeled_count} />
      <MiniStat label="成功数" value={overview.success_count} />
      <MiniStat label="命中率" value={`${(overview.hit_rate * 100).toFixed(1)}%`} />
      <MiniStat label="均收益" value={`${overview.avg_return.toFixed(2)}%`} />
      <MiniStat label="均回撤" value={`${overview.avg_drawdown.toFixed(2)}%`} />
      <MiniStat label="观察项" value={overview.watch_count} />
    </div>
  );
}

function StatsTable({
  rows,
  nameKey,
  subKey,
  countKey,
}: {
  rows: Array<Record<string, unknown>>;
  nameKey: string;
  subKey: string;
  countKey: string;
}) {
  if (!rows.length) {
    return <EmptyHint text="暂无统计样本" />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>对象</th>
            <th>样本</th>
            <th>胜率</th>
            <th>平均收益</th>
            <th>有效切面</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row[subKey] ?? row[nameKey])}>
              <td>
                <strong>{String(row[nameKey] ?? "-")}</strong>
                <span>{String(row[subKey] ?? "-")}</span>
              </td>
              <td>{String(row[countKey] ?? 0)}</td>
              <td>{`${(Number(row.hit_rate ?? 0) * 100).toFixed(1)}%`}</td>
              <td>{`${Number(row.avg_return ?? 0).toFixed(2)}%`}</td>
              <td>
                <ScenarioTags row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScenarioTags({ row }: { row: Record<string, unknown> }) {
  const items = [
    ...asScenarioRows(row.top_market_regimes).slice(0, 1),
    ...asScenarioRows(row.top_industries).slice(0, 1),
    ...asScenarioRows(row.top_setups).slice(0, 1),
  ];
  if (!items.length) return <span>-</span>;
  return (
    <div className="compact-tags">
      {items.map((item) => (
        <span key={`${item.name}-${item.count}`} className="mini-chip">
          {item.name} {item.count}
        </span>
      ))}
    </div>
  );
}

function asScenarioRows(value: unknown): Array<{ name: string; count: number }> {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const row = item as Record<string, unknown>;
    return { name: String(row.name ?? ""), count: Number(row.count ?? 0) };
  }).filter((item) => item.name);
}
