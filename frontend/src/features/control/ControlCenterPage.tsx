import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  DatabaseZap,
  KeyRound,
  Loader2,
  PauseCircle,
  Play,
  Plus,
  RefreshCcw,
  Save,
  SquarePen,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import type {
  LocalPoolStatus,
  ModelRoute,
  Preset,
  RunSummary,
  ScheduledTask,
  ScheduledTaskPayload,
  ScheduledTaskRun,
  ScheduledTaskType,
  SkillSummary,
} from "../../types";

type TaskDraft = {
  task_id: string;
  name: string;
  task_type: ScheduledTaskType;
  trigger_time: string;
  preset_id: string;
  enabled: boolean;
};

type RouteDraft = {
  name: string;
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
  enabled: boolean;
  clear_api_key: boolean;
};

type PresetDraft = {
  name: string;
  description: string;
  run_mode: Preset["run_mode"];
  market_scope: string;
  market_scope_value: string;
  risk_template: string;
  score_line: number;
  data_source: ManualRunDataSource;
  hide_recent_repeats: boolean;
};

type PresetSkillDraft = {
  skill_id: string;
  weight: number;
  sort_order: number;
};

type SectionLoadState = {
  presets: boolean;
  skills: boolean;
  tasks: boolean;
  taskRuns: boolean;
  modelRoutes: boolean;
  pool: boolean;
};

type ManualRunDataSource = "local_pool" | "local_pool_preferred" | "realtime";

const EMPTY_TASK_DRAFT: TaskDraft = {
  task_id: "",
  name: "",
  task_type: "recommendation_run",
  trigger_time: "09:05",
  preset_id: "",
  enabled: true,
};

const EMPTY_ROUTE_DRAFT: RouteDraft = {
  name: "",
  provider: "openai_compatible",
  base_url: "",
  model: "",
  api_key: "",
  enabled: true,
  clear_api_key: false,
};

const EMPTY_PRESET_DRAFT: PresetDraft = {
  name: "",
  description: "",
  run_mode: "formal",
  market_scope: "all",
  market_scope_value: "",
  risk_template: "balanced",
  score_line: 72,
  data_source: "local_pool",
  hide_recent_repeats: true,
};

const TERMINAL_RUN_STATUS = new Set(["completed", "failed", "interrupted", "cancelled"]);
const INITIAL_SECTION_LOADING: SectionLoadState = {
  presets: true,
  skills: true,
  tasks: true,
  taskRuns: true,
  modelRoutes: true,
  pool: true,
};

export default function ControlCenterPage() {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [taskRuns, setTaskRuns] = useState<ScheduledTaskRun[]>([]);
  const [modelRoutes, setModelRoutes] = useState<ModelRoute[]>([]);
  const [pool, setPool] = useState<LocalPoolStatus | null>(null);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedRunDataSource, setSelectedRunDataSource] = useState<ManualRunDataSource>("local_pool");
  const [latestRun, setLatestRun] = useState<RunSummary | null>(null);
  const [taskDraft, setTaskDraft] = useState<TaskDraft>(EMPTY_TASK_DRAFT);
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [routeDraft, setRouteDraft] = useState<RouteDraft>(EMPTY_ROUTE_DRAFT);
  const [editingRouteId, setEditingRouteId] = useState<string | null>(null);
  const [presetDraft, setPresetDraft] = useState<PresetDraft>(EMPTY_PRESET_DRAFT);
  const [presetSkillDrafts, setPresetSkillDrafts] = useState<PresetSkillDraft[]>([]);
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [sectionLoading, setSectionLoading] = useState<SectionLoadState>(INITIAL_SECTION_LOADING);
  const [running, setRunning] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [savingTask, setSavingTask] = useState(false);
  const [savingRoute, setSavingRoute] = useState(false);
  const [savingPreset, setSavingPreset] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const selectedPreset = useMemo(
    () => presets.find((preset) => preset.preset_id === selectedPresetId) ?? presets[0] ?? null,
    [presets, selectedPresetId],
  );
  const activeTasks = tasks.filter((task) => task.enabled).length;
  const usableRoutes = modelRoutes.filter((route) => route.enabled && route.has_api_key).length;
  const currentTask = tasks.find((task) => task.task_id === editingTaskId) ?? null;
  const currentRoute = modelRoutes.find((route) => route.route_id === editingRouteId) ?? null;
  const readiness = pool?.recommendation_readiness;
  const isRefreshingState = Object.values(sectionLoading).some(Boolean);

  const loadControlState = useCallback(async () => {
    setError("");
    setSectionLoading(INITIAL_SECTION_LOADING);

    const errors: string[] = [];
    const finishSection = (section: keyof SectionLoadState) => {
      setSectionLoading((current) => ({ ...current, [section]: false }));
    };
    const captureError = (label: string, err: unknown) => {
      errors.push(`${label}加载失败：${messageFromError(err)}`);
    };

    await Promise.all([
      (async () => {
        try {
          const presetRows = await api.presets();
          setPresets(presetRows);
          setSelectedPresetId((current) => current || presetRows[0]?.preset_id || "");
          setSelectedRunDataSource((current) => (current || (presetRows[0]?.data_source as ManualRunDataSource) || "local_pool"));
          setPresetDraft((current) => current.name ? current : buildPresetDraft(null, presetRows.length));
          setPresetSkillDrafts((current) => current);
          setTaskDraft((current) => ({
            ...current,
            preset_id: current.preset_id || presetRows[0]?.preset_id || "",
          }));
        } catch (err) {
          captureError("预设", err);
        } finally {
          finishSection("presets");
        }
      })(),
      (async () => {
        try {
          const skillRows = await api.skills();
          setSkills(skillRows.filter((skill) => skill.enabled));
        } catch (err) {
          captureError("策略", err);
        } finally {
          finishSection("skills");
        }
      })(),
      (async () => {
        try {
          setTasks(await api.scheduledTasks());
        } catch (err) {
          captureError("自动任务", err);
        } finally {
          finishSection("tasks");
        }
      })(),
      (async () => {
        try {
          setTaskRuns(await api.scheduledTaskRuns(30));
        } catch (err) {
          captureError("任务运行记录", err);
        } finally {
          finishSection("taskRuns");
        }
      })(),
      (async () => {
        try {
          setModelRoutes(await api.modelRoutes());
        } catch (err) {
          captureError("模型路由", err);
        } finally {
          finishSection("modelRoutes");
        }
      })(),
      (async () => {
        try {
          setPool(await api.localPool({ u_limit: 5, b_limit: 5, include_details: false }));
        } catch (err) {
          captureError("本地数据池", err);
        } finally {
          finishSection("pool");
        }
      })(),
    ]);

    if (errors.length) {
      setError(errors.join("；"));
    }
  }, []);

  useEffect(() => {
    void loadControlState();
  }, [loadControlState]);

  async function runSelectedPreset() {
    if (!selectedPreset) {
      setError("请先选择一个推荐预设");
      return;
    }
    setRunning(true);
    setError("");
    setMessage("");
    try {
      const result = await api.createRun({
        preset_id: selectedPreset.preset_id,
        run_name: `${selectedPreset.name} · 手动推荐`,
        data_source: selectedRunDataSource,
        async: true,
      });
      setLatestRun(result.run);
      setMessage(`推荐已触发：${result.run.run_id}`);
      if (!TERMINAL_RUN_STATUS.has(result.run.status)) {
        await pollRun(result.run.run_id);
      }
      await loadControlState();
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setRunning(false);
    }
  }

  async function pollRun(runId: string) {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await delay(3000);
      const run = await api.runDetail(runId);
      setLatestRun(run);
      if (TERMINAL_RUN_STATUS.has(run.status)) {
        setMessage(run.status === "completed" ? `推荐完成：输出 ${run.recommended_count} 只` : `推荐结束：${run.status}`);
        return;
      }
    }
    setMessage("推荐已提交，仍在后台运行，请稍后刷新运行状态");
  }

  async function refreshLocalPool() {
    setSyncing(true);
    setError("");
    setMessage("");
    try {
      const result = await api.refreshLocalPool();
      const poolStatus = await api.localPool({ u_limit: 5, b_limit: 5, include_details: false });
      setPool(poolStatus);
      setMessage(`数据池刷新完成：${result.refreshed} 条报价`);
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setSyncing(false);
    }
  }

  function editTask(task: ScheduledTask) {
    setEditingTaskId(task.task_id);
    setTaskDraft({
      task_id: task.task_id,
      name: task.name,
      task_type: task.task_type,
      trigger_time: task.trigger_time ?? cronToClockTime(task.cron_expression) ?? "09:05",
      preset_id: String(task.payload?.preset_id ?? selectedPresetId),
      enabled: task.enabled,
    });
  }

  function resetTaskDraft() {
    setEditingTaskId(null);
    setTaskDraft({ ...EMPTY_TASK_DRAFT, preset_id: selectedPresetId || presets[0]?.preset_id || "" });
  }

  async function saveTask() {
    if (taskDraft.task_type === "recommendation_run" && !taskDraft.preset_id && taskDraft.enabled) {
      setError("推荐任务需要选择预设");
      return;
    }
    setSavingTask(true);
    setError("");
    setMessage("");
    try {
      const taskId = (editingTaskId ?? taskDraft.task_id.trim()) || makeTaskId(taskDraft);
      const payload: ScheduledTaskPayload = {
        task_id: taskId,
        name: taskDraft.name.trim() || taskLabel(taskDraft.task_type),
        task_type: taskDraft.task_type,
        trigger_time: taskDraft.trigger_time || "09:05",
        payload: taskDraft.task_type === "recommendation_run"
          ? { preset_id: taskDraft.preset_id, run_name: `${taskDraft.name.trim() || "自动推荐"} · 自动推荐` }
          : {},
        enabled: taskDraft.enabled,
      };
      const saved = editingTaskId
        ? await api.updateScheduledTask(editingTaskId, payload)
        : await api.createScheduledTask(payload);
      setEditingTaskId(saved.task_id);
      editTask(saved);
      setMessage("自动任务已保存");
      await reloadTasks();
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setSavingTask(false);
    }
  }

  async function toggleTask(task: ScheduledTask) {
    setError("");
    try {
      await api.updateScheduledTask(task.task_id, { ...taskToPayload(task), enabled: !task.enabled });
      setMessage(`${task.name} ${task.enabled ? "已停用" : "已启用"}`);
      await reloadTasks();
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function runTaskNow(task: ScheduledTask) {
    setError("");
    try {
      await api.runScheduledTask(task.task_id);
      setMessage(`${task.name} 已立即执行`);
      await reloadTasks();
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function deleteTask(taskId: string) {
    if (!window.confirm("确定删除这个自动任务？")) return;
    setError("");
    try {
      await api.deleteScheduledTask(taskId);
      if (editingTaskId === taskId) resetTaskDraft();
      setMessage("自动任务已删除");
      await reloadTasks();
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function reloadTasks() {
    const [taskRows, taskRunRows] = await Promise.allSettled([api.scheduledTasks(), api.scheduledTaskRuns(30)]);
    const errors: string[] = [];
    if (taskRows.status === "fulfilled") {
      setTasks(taskRows.value);
    } else {
      errors.push(`自动任务刷新失败：${messageFromError(taskRows.reason)}`);
    }
    if (taskRunRows.status === "fulfilled") {
      setTaskRuns(taskRunRows.value);
    } else {
      errors.push(`任务运行记录刷新失败：${messageFromError(taskRunRows.reason)}`);
    }
    if (errors.length) {
      setError(errors.join("；"));
    }
  }

  function startBlankPreset() {
    setEditingPresetId(null);
    setPresetDraft(buildPresetDraft(null, presets.length));
    setPresetSkillDrafts([]);
  }

  function resetPresetDraft() {
    if (editingPresetId) {
      const editingPreset = presets.find((preset) => preset.preset_id === editingPresetId) ?? null;
      setPresetDraft(buildPresetDraft(editingPreset, presets.length));
      setPresetSkillDrafts(buildPresetSkillDrafts(editingPreset));
      return;
    }
    startBlankPreset();
  }

  function editPreset(preset: Preset) {
    setEditingPresetId(preset.preset_id);
    setSelectedPresetId(preset.preset_id);
    setSelectedRunDataSource(normalizeManualRunDataSource(preset.data_source));
    setPresetDraft(buildPresetDraft(preset, presets.length));
    setPresetSkillDrafts(buildPresetSkillDrafts(preset));
  }

  async function deletePreset(preset: Preset) {
    if (!window.confirm(`确定删除预设“${preset.name}”？`)) return;
    setError("");
    setMessage("");
    try {
      await api.deletePreset(preset.preset_id);
      const presetRows = await api.presets();
      const fallbackPreset = presetRows[0] ?? null;
      setPresets(presetRows);
      if (editingPresetId === preset.preset_id) {
        setEditingPresetId(null);
        setPresetDraft(buildPresetDraft(null, presetRows.length));
        setPresetSkillDrafts([]);
      }
      if (selectedPresetId === preset.preset_id) {
        setSelectedPresetId(fallbackPreset?.preset_id ?? "");
        setSelectedRunDataSource(normalizeManualRunDataSource(fallbackPreset?.data_source));
      }
      setTaskDraft((current) => (
        current.preset_id === preset.preset_id
          ? { ...current, preset_id: fallbackPreset?.preset_id ?? "" }
          : current
      ));
      setMessage(`预设已删除：${preset.name}`);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function savePreset() {
    if (!presetDraft.name.trim()) {
      setError("请填写预设名称");
      return;
    }
    if (!presetSkillDrafts.length) {
      setError("至少选择一个策略后才能保存预设");
      return;
    }
    setSavingPreset(true);
    setError("");
    setMessage("");
    try {
      const payload: Record<string, unknown> = {
        name: presetDraft.name.trim(),
        description: presetDraft.description.trim() || null,
        run_mode: presetDraft.run_mode,
        market_scope: presetDraft.market_scope,
        market_scope_value: presetDraft.market_scope_value.trim() || null,
        risk_template: presetDraft.risk_template,
        score_line: presetDraft.score_line,
        data_source: presetDraft.data_source,
        hide_recent_repeats: presetDraft.hide_recent_repeats,
        skills: normalizePresetSkillDrafts(presetSkillDrafts).map((skill) => ({
          skill_id: skill.skill_id,
          weight: skill.weight,
          sort_order: skill.sort_order,
        })),
      };
      const saved = editingPresetId
        ? await api.updatePreset(editingPresetId, payload)
        : await api.createPreset(payload);
      const presetRows = await api.presets();
      setPresets(presetRows);
      setSelectedPresetId(saved.preset_id);
      setSelectedRunDataSource(normalizeManualRunDataSource(saved.data_source));
      setEditingPresetId(saved.preset_id);
      setTaskDraft((current) => (
        current.preset_id
          ? current
          : { ...current, preset_id: saved.preset_id }
      ));
      setPresetDraft(buildPresetDraft(saved, presetRows.length));
      setPresetSkillDrafts(buildPresetSkillDrafts(saved));
      setMessage(editingPresetId ? `预设已更新：${saved.name}` : `预设已创建：${saved.name}`);
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setSavingPreset(false);
    }
  }

  function togglePresetSkill(skill: SkillSummary) {
    setPresetSkillDrafts((current) => {
      const exists = current.some((item) => item.skill_id === skill.skill_id);
      if (exists) {
        return normalizePresetSkillDrafts(current.filter((item) => item.skill_id !== skill.skill_id));
      }
      return normalizePresetSkillDrafts([
        ...current,
        {
          skill_id: skill.skill_id,
          weight: 1,
          sort_order: current.length + 1,
        },
      ]);
    });
  }

  function updatePresetSkillWeight(skillId: string, weight: number) {
    setPresetSkillDrafts((current) => normalizePresetSkillDrafts(current.map((item) => (
      item.skill_id === skillId
        ? { ...item, weight: Number.isFinite(weight) && weight > 0 ? weight : item.weight }
        : item
    ))));
  }

  function startCreateRoute() {
    setEditingRouteId(null);
    setRouteDraft({
      ...EMPTY_ROUTE_DRAFT,
      name: `模型路由 ${modelRoutes.length + 1}`,
      base_url: modelRoutes[0]?.base_url ?? "https://api.deepseek.com",
      model: modelRoutes[0]?.model ?? "deepseek-chat",
    });
  }

  function editRoute(route: ModelRoute) {
    setEditingRouteId(route.route_id);
    setRouteDraft({
      name: route.name,
      provider: route.provider || "openai_compatible",
      base_url: route.base_url,
      model: route.model,
      api_key: "",
      enabled: route.enabled,
      clear_api_key: false,
    });
  }

  function resetRouteDraft() {
    setEditingRouteId(null);
    setRouteDraft(EMPTY_ROUTE_DRAFT);
  }

  async function saveRoute() {
    if (!routeDraft.base_url.trim() || !routeDraft.model.trim()) {
      setError("模型路由需要接口地址和模型名");
      return;
    }
    setSavingRoute(true);
    setError("");
    setMessage("");
    try {
      const payload: Record<string, unknown> = {
        name: routeDraft.name.trim() || "未命名模型路由",
        provider: routeDraft.provider || "openai_compatible",
        base_url: routeDraft.base_url.trim(),
        model: routeDraft.model.trim(),
        enabled: routeDraft.enabled,
      };
      if (routeDraft.api_key.trim()) payload.api_key = routeDraft.api_key.trim();
      if (routeDraft.clear_api_key) payload.clear_api_key = true;
      const saved = editingRouteId
        ? await api.updateModelRoute(editingRouteId, payload)
        : await api.createModelRoute(payload);
      setEditingRouteId(saved.route_id);
      editRoute(saved);
      setMessage("模型路由已保存");
      setModelRoutes(await api.modelRoutes());
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setSavingRoute(false);
    }
  }

  async function toggleRoute(route: ModelRoute) {
    setError("");
    try {
      await api.updateModelRoute(route.route_id, { enabled: !route.enabled });
      setModelRoutes(await api.modelRoutes());
      setMessage(`${route.name} ${route.enabled ? "已停用" : "已启用"}`);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function deleteRoute(routeId: string) {
    if (!window.confirm("确定删除这条模型路由？")) return;
    setError("");
    try {
      await api.deleteModelRoute(routeId);
      if (editingRouteId === routeId) resetRouteDraft();
      setModelRoutes(await api.modelRoutes());
      setMessage("模型路由已删除");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function moveRoute(routeId: string, direction: "up" | "down") {
    const currentIndex = modelRoutes.findIndex((route) => route.route_id === routeId);
    const nextIndex = direction === "up" ? currentIndex - 1 : currentIndex + 1;
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= modelRoutes.length) return;
    const ordered = [...modelRoutes];
    [ordered[currentIndex], ordered[nextIndex]] = [ordered[nextIndex], ordered[currentIndex]];
    setModelRoutes(await api.reorderModelRoutes(ordered.map((route) => route.route_id)));
  }

  function handlePresetChange(presetId: string) {
    setSelectedPresetId(presetId);
    const nextPreset = presets.find((preset) => preset.preset_id === presetId);
    setSelectedRunDataSource((nextPreset?.data_source as ManualRunDataSource) || "local_pool");
  }

  return (
    <div className="workspace-grid control-center">
      <section className="section-hero">
        <div>
          <span>控制中心</span>
          <h2>推荐、数据、调度与模型门禁</h2>
          <p>把影响短线推荐链路的操作集中在同一个驾驶舱入口，避免靠命令行触发关键流程。</p>
        </div>
        <button className="control-button" type="button" onClick={() => void loadControlState()} disabled={isRefreshingState}>
          {isRefreshingState ? <Loader2 size={15} className="spin" /> : <RefreshCcw size={15} />}
          {isRefreshingState ? "刷新中" : "刷新状态"}
        </button>
      </section>

      {error ? <div className="control-banner danger">{error}</div> : null}
      {message ? <div className="control-banner positive">{message}</div> : null}

      <div className="control-summary">
        <SummaryTile label="预设" value={presets.length} note={selectedPreset?.name ?? "未选择"} />
        <SummaryTile label="可用模型" value={usableRoutes} note={`${modelRoutes.length} 条路由`} />
        <SummaryTile label="启用任务" value={activeTasks} note={`${tasks.length} 个任务`} />
        <SummaryTile
          label="本地池"
          value={readiness?.can_run_local_recommendation ? "READY" : "CHECK"}
          note={readiness?.status ?? "unknown"}
        />
      </div>

      <div className="control-grid">
        <section className="cockpit-panel control-panel">
          <PanelHeader kicker="Run" title="手动触发推荐" />
          <div className="control-form">
            <label className="control-field">
              <span>运行预设</span>
              <select
                value={selectedPreset?.preset_id ?? ""}
                onChange={(event) => handlePresetChange(event.target.value)}
                disabled={sectionLoading.presets}
              >
                {presets.map((preset) => (
                  <option key={preset.preset_id} value={preset.preset_id}>
                    {preset.name} · {labelText(preset.run_mode)}
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
            {sectionLoading.presets ? <div className="control-empty">预设加载中</div> : null}
            {selectedPreset ? (
              <div className="mini-table">
                <span>模式<strong>{labelText(selectedPreset.run_mode)}</strong></span>
                <span>分数线<strong>{selectedPreset.score_line.toFixed(1)}</strong></span>
                <span>数据源<strong>{labelText(selectedRunDataSource)}</strong></span>
                <span>Skill<strong>{selectedPreset.skills.length}</strong></span>
              </div>
            ) : null}
            <button className="control-button primary wide" type="button" onClick={() => void runSelectedPreset()} disabled={running || !selectedPreset}>
              {running ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
              {running ? "推荐运行中" : "立即运行推荐"}
            </button>
          </div>
          {latestRun ? <RunStatusCard run={latestRun} /> : <div className="control-empty">暂无本页触发的推荐运行</div>}
        </section>

        <section className="cockpit-panel control-panel">
          <PanelHeader kicker="Data" title="数据同步" />
          <div className="metric-grid">
            <span>统一报价<strong>{pool?.summary.active_unified_quote_count ?? 0}</strong></span>
            <span>合格日K<strong>{pool?.summary.eligible_daily_bar_count ?? 0}</strong></span>
            <span>Skill 输入<strong>{pool?.summary.skill_input_count ?? 0}</strong></span>
            <span>覆盖日期<strong>{pool?.summary.coverage_trade_date ?? "-"}</strong></span>
          </div>
          <button className="control-button primary wide" type="button" onClick={() => void refreshLocalPool()} disabled={syncing}>
            {syncing ? <Loader2 size={15} className="spin" /> : <DatabaseZap size={15} />}
            {syncing ? "同步中" : "刷新本地数据池"}
          </button>
          <div className="alert-stream">
            {(sectionLoading.pool
              ? ["本地数据池加载中"]
              : readiness?.messages?.length
                ? readiness.messages
                : ["暂无数据池消息"]
            ).slice(0, 5).map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </section>
      </div>

      <div className="control-grid wide-left">
        <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="Preset"
            title="预设管理"
            action={
              <button className="control-button subtle" type="button" onClick={startBlankPreset}>
                <Plus size={14} />
                空白新增
              </button>
            }
          />
          <div className="task-list">
            {presets.length ? presets.map((preset) => (
              <article key={preset.preset_id} className={`control-row ${selectedPreset?.preset_id === preset.preset_id ? "active" : ""}`}>
                <button type="button" className="row-main-button" onClick={() => handlePresetChange(preset.preset_id)}>
                  <strong>{preset.name}</strong>
                  <span>{labelText(preset.run_mode)} · {describePresetScope(preset)} · {labelText(preset.data_source)}</span>
                  <small>{preset.description || `分数线 ${preset.score_line.toFixed(1)} · ${preset.skills.length} 个 Skill`}</small>
                </button>
                <div className="row-actions">
                  <button type="button" className="icon-button" title="编辑" onClick={() => editPreset(preset)}>
                    <SquarePen size={14} />
                  </button>
                  <button type="button" className="icon-button danger" title="删除" onClick={() => void deletePreset(preset)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </article>
            )) : <div className="control-empty">{sectionLoading.presets ? "预设加载中" : "暂无预设"}</div>}
          </div>
          <div className="control-note">
            <span>这里现在支持空白新增、编辑和删除。新增时记得至少勾一个策略，建完才能直接运行。</span>
          </div>
        </section>

        <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="Create"
            title={editingPresetId ? "编辑预设" : "新增预设"}
            action={
              <button className="control-button subtle" type="button" onClick={editingPresetId ? startBlankPreset : resetPresetDraft}>
                {editingPresetId ? <Plus size={14} /> : <X size={14} />}
                {editingPresetId ? "新建" : "重置"}
              </button>
            }
          />
          <div className="control-form">
            <label className="control-field">
              <span>预设名称</span>
              <input
                value={presetDraft.name}
                placeholder="比如：隔夜持股法 - 实盘观察"
                onChange={(event) => setPresetDraft((current) => ({ ...current, name: event.target.value }))}
              />
            </label>
            <label className="control-field">
              <span>描述</span>
              <input
                value={presetDraft.description}
                placeholder="写给自己看的备注"
                onChange={(event) => setPresetDraft((current) => ({ ...current, description: event.target.value }))}
              />
            </label>
            <label className="control-field">
              <span>运行模式</span>
              <select
                value={presetDraft.run_mode}
                onChange={(event) => setPresetDraft((current) => ({ ...current, run_mode: event.target.value as Preset["run_mode"] }))}
              >
                <option value="formal">正式</option>
                <option value="experiment">实验</option>
              </select>
            </label>
            <label className="control-field">
              <span>市场范围</span>
              <select
                value={presetDraft.market_scope}
                onChange={(event) => setPresetDraft((current) => ({ ...current, market_scope: event.target.value }))}
              >
                <option value="all">全市场</option>
                <option value="watchlist">自选股</option>
                <option value="industry">行业</option>
              </select>
            </label>
            <label className="control-field">
              <span>范围值</span>
              <input
                value={presetDraft.market_scope_value}
                placeholder={presetDraft.market_scope === "industry" ? "例如：电气设备" : presetDraft.market_scope === "watchlist" ? "例如：观察池A" : "全市场可留空"}
                onChange={(event) => setPresetDraft((current) => ({ ...current, market_scope_value: event.target.value }))}
              />
            </label>
            <label className="control-field">
              <span>风控模板</span>
              <select
                value={presetDraft.risk_template}
                onChange={(event) => setPresetDraft((current) => ({ ...current, risk_template: event.target.value }))}
              >
                <option value="conservative">保守</option>
                <option value="balanced">均衡</option>
                <option value="aggressive">激进</option>
              </select>
            </label>
            <label className="control-field">
              <span>分数线</span>
              <input
                type="number"
                min={35}
                max={95}
                step={0.5}
                value={presetDraft.score_line}
                onChange={(event) => {
                  const nextValue = Number(event.target.value);
                  setPresetDraft((current) => ({
                    ...current,
                    score_line: Number.isFinite(nextValue) ? nextValue : current.score_line,
                  }));
                }}
              />
            </label>
            <label className="control-field">
              <span>数据源</span>
              <select
                value={presetDraft.data_source}
                onChange={(event) => setPresetDraft((current) => ({ ...current, data_source: event.target.value as ManualRunDataSource }))}
              >
                <option value="local_pool">仅本地池</option>
                <option value="local_pool_preferred">本地池优先</option>
                <option value="realtime">实时数据</option>
              </select>
            </label>
            <label className="control-toggle">
              <span>隐藏近期重复票</span>
              <button type="button" className={presetDraft.hide_recent_repeats ? "on" : ""} onClick={() => setPresetDraft((current) => ({ ...current, hide_recent_repeats: !current.hide_recent_repeats }))}>
                {presetDraft.hide_recent_repeats ? "ON" : "OFF"}
              </button>
            </label>
            <div className="control-form">
              <div className="panel-kicker">策略组合</div>
              {sectionLoading.skills ? <div className="control-empty">策略列表加载中</div> : null}
              {!sectionLoading.skills ? (
                skills.length ? (
                  <div className="task-list">
                    {skills.map((skill) => {
                      const selected = presetSkillDrafts.find((item) => item.skill_id === skill.skill_id) ?? null;
                      return (
                        <article key={skill.skill_id} className={`control-row ${selected ? "active" : ""}`}>
                          <div className="row-main-button">
                            <strong>{skill.name}</strong>
                            <span>{skill.skill_id} · {labelText(skill.mode)}</span>
                          </div>
                          <div className="row-actions">
                            {selected ? (
                              <input
                                className="preset-weight-input"
                                type="number"
                                min={0.1}
                                max={5}
                                step={0.05}
                                value={selected.weight}
                                onChange={(event) => updatePresetSkillWeight(skill.skill_id, Number(event.target.value))}
                              />
                            ) : null}
                            <button className={`control-button ${selected ? "primary" : "subtle"}`} type="button" onClick={() => togglePresetSkill(skill)}>
                              {selected ? "移除" : "加入"}
                            </button>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                ) : (
                  <div className="control-empty">暂无可用策略</div>
                )
              ) : null}
            </div>
            <div className="mini-table">
              <span>状态<strong>{editingPresetId ? "编辑中" : "新增中"}</strong></span>
              <span>已选策略<strong>{presetSkillDrafts.length}</strong></span>
              <span>模式<strong>{labelText(presetDraft.run_mode)}</strong></span>
              <span>数据源<strong>{labelText(presetDraft.data_source)}</strong></span>
            </div>
            {editingPresetId ? (
              <div className="control-note">
                <span>当前是编辑模式，参数和策略组合都可以一起改。</span>
              </div>
            ) : null}
            <button className="control-button primary wide" type="button" onClick={() => void savePreset()} disabled={savingPreset || sectionLoading.skills}>
              {savingPreset ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
              {savingPreset ? "保存中" : editingPresetId ? "保存修改" : "创建预设"}
            </button>
          </div>
        </section>
      </div>

      <div className="control-grid wide-left">
        <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="Schedule"
            title="自动任务"
            action={
              <button className="control-button subtle" type="button" onClick={resetTaskDraft}>
                <Plus size={14} />
                新建
              </button>
            }
          />
          <div className="task-list">
            {tasks.length ? tasks.map((task) => (
              <article key={task.task_id} className={`control-row ${editingTaskId === task.task_id ? "active" : ""}`}>
                <button type="button" className="row-main-button" onClick={() => editTask(task)}>
                  <strong>{task.name}</strong>
                  <span>{taskLabel(task.task_type)} · {formatTaskTime(task)} · {describeTaskPayload(task, presets)}</span>
                  {task.last_error ? <small className="negative">{task.last_error}</small> : null}
                </button>
                <div className="row-actions">
                  <button type="button" className="icon-button" title="立即执行" onClick={() => void runTaskNow(task)} disabled={task.is_running}>
                    <Play size={14} />
                  </button>
                  <button type="button" className="icon-button" title={task.enabled ? "停用" : "启用"} onClick={() => void toggleTask(task)}>
                    <PauseCircle size={14} />
                  </button>
                  <button type="button" className="icon-button danger" title="删除" onClick={() => void deleteTask(task.task_id)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </article>
            )) : <div className="control-empty">{sectionLoading.tasks ? "自动任务加载中" : "暂无自动任务"}</div>}
          </div>
          <TaskRunStrip rows={taskRuns} loading={sectionLoading.taskRuns} />
        </section>

        <section className="cockpit-panel control-panel">
          <PanelHeader kicker="Task" title={editingTaskId ? "编辑任务" : "新建任务"} />
          <div className="control-form">
            <label className="control-field">
              <span>任务名称</span>
              <input value={taskDraft.name} onChange={(event) => setTaskDraft((current) => ({ ...current, name: event.target.value }))} />
            </label>
            <label className="control-field">
              <span>触发时间</span>
              <input type="time" value={taskDraft.trigger_time} onChange={(event) => setTaskDraft((current) => ({ ...current, trigger_time: event.target.value }))} />
            </label>
            <label className="control-field">
              <span>任务类型</span>
              <select value={taskDraft.task_type} onChange={(event) => setTaskDraft((current) => ({ ...current, task_type: event.target.value as ScheduledTaskType }))}>
                <option value="recommendation_run">股票推荐</option>
                <option value="pool_refresh">行情池刷新</option>
                <option value="data_sync">数据同步</option>
                <option value="data_backfill">数据回填</option>
              </select>
            </label>
            {taskDraft.task_type === "recommendation_run" ? (
              <label className="control-field">
                <span>推荐预设</span>
                <select
                  value={taskDraft.preset_id}
                  onChange={(event) => setTaskDraft((current) => ({ ...current, preset_id: event.target.value }))}
                  disabled={sectionLoading.presets}
                >
                  <option value="">请选择预设</option>
                  {presets.map((preset) => (
                    <option key={preset.preset_id} value={preset.preset_id}>
                      {preset.name} · {labelText(preset.run_mode)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="control-toggle">
              <span>启用任务</span>
              <button type="button" className={taskDraft.enabled ? "on" : ""} onClick={() => setTaskDraft((current) => ({ ...current, enabled: !current.enabled }))}>
                {taskDraft.enabled ? "ON" : "OFF"}
              </button>
            </label>
            <button className="control-button primary wide" type="button" onClick={() => void saveTask()} disabled={savingTask}>
              {savingTask ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
              保存任务
            </button>
          </div>
        </section>
      </div>

      <div className="control-grid wide-left">
        <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="Model"
            title="模型路由与 API Key"
            action={
              <button className="control-button subtle" type="button" onClick={startCreateRoute}>
                <Plus size={14} />
                新增
              </button>
            }
          />
          <div className="task-list">
            {modelRoutes.length ? modelRoutes.map((route, index) => (
              <article key={route.route_id} className={`control-row ${editingRouteId === route.route_id ? "active" : ""}`}>
                <button type="button" className="row-main-button" onClick={() => editRoute(route)}>
                  <strong>{route.name}</strong>
                  <span>{route.model} · {route.base_url || "-"} · {routeKeyLabel(route)}</span>
                </button>
                <div className="row-actions">
                  <button type="button" className="icon-button" title="上移" disabled={index === 0} onClick={() => void moveRoute(route.route_id, "up")}>
                    <ArrowUp size={14} />
                  </button>
                  <button type="button" className="icon-button" title="下移" disabled={index === modelRoutes.length - 1} onClick={() => void moveRoute(route.route_id, "down")}>
                    <ArrowDown size={14} />
                  </button>
                  <button type="button" className="icon-button" title="编辑" onClick={() => editRoute(route)}>
                    <SquarePen size={14} />
                  </button>
                  <button type="button" className="icon-button" title={route.enabled ? "停用" : "启用"} onClick={() => void toggleRoute(route)}>
                    <PauseCircle size={14} />
                  </button>
                  <button type="button" className="icon-button danger" title="删除" onClick={() => void deleteRoute(route.route_id)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </article>
            )) : <div className="control-empty">{sectionLoading.modelRoutes ? "模型路由加载中" : "暂无模型路由"}</div>}
          </div>
        </section>

        <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="Key"
            title={currentRoute ? `编辑 ${currentRoute.name}` : "新增模型路由"}
            action={
              <button className="control-button subtle" type="button" onClick={resetRouteDraft}>
                <X size={14} />
                清空
              </button>
            }
          />
          <div className="control-form">
            <label className="control-field">
              <span>名称</span>
              <input value={routeDraft.name} onChange={(event) => setRouteDraft((current) => ({ ...current, name: event.target.value }))} />
            </label>
            <label className="control-field">
              <span>接口地址</span>
              <input value={routeDraft.base_url} placeholder="https://api.deepseek.com" onChange={(event) => setRouteDraft((current) => ({ ...current, base_url: event.target.value }))} />
            </label>
            <label className="control-field">
              <span>模型名</span>
              <input value={routeDraft.model} placeholder="deepseek-chat" onChange={(event) => setRouteDraft((current) => ({ ...current, model: event.target.value }))} />
            </label>
            <label className="control-field">
              <span>{editingRouteId ? "新 API Key" : "API Key"}</span>
              <input type="password" value={routeDraft.api_key} placeholder={editingRouteId ? "留空表示不改" : "未填则走系统默认 Key"} onChange={(event) => setRouteDraft((current) => ({ ...current, api_key: event.target.value }))} />
            </label>
            <label className="control-toggle">
              <span>启用路由</span>
              <button type="button" className={routeDraft.enabled ? "on" : ""} onClick={() => setRouteDraft((current) => ({ ...current, enabled: !current.enabled }))}>
                {routeDraft.enabled ? "ON" : "OFF"}
              </button>
            </label>
            {editingRouteId ? (
              <label className="control-toggle">
                <span>清空自定义 Key</span>
                <button type="button" className={routeDraft.clear_api_key ? "on" : ""} onClick={() => setRouteDraft((current) => ({ ...current, clear_api_key: !current.clear_api_key }))}>
                  {routeDraft.clear_api_key ? "ON" : "OFF"}
                </button>
              </label>
            ) : null}
            <div className="control-note">
              <KeyRound size={14} />
              <span>{currentRoute ? `当前状态：${routeKeyLabel(currentRoute)}` : "未填写 API Key 时，会尝试使用系统默认 Key。"}</span>
            </div>
            <button className="control-button primary wide" type="button" onClick={() => void saveRoute()} disabled={savingRoute}>
              {savingRoute ? <Loader2 size={15} className="spin" /> : <CheckCircle2 size={15} />}
              保存模型路由
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

function PanelHeader({ kicker, title, action }: { kicker: string; title: string; action?: ReactNode }) {
  return (
    <div className="panel-header">
      <div>
        <div className="panel-kicker">{kicker}</div>
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}

function SummaryTile({ label, value, note }: { label: string; value: string | number; note: string }) {
  return (
    <article className="cockpit-panel control-summary-tile">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}

function RunStatusCard({ run }: { run: RunSummary }) {
  const pct = progressPercent(run);
  return (
    <div className="run-status-card">
      <div>
        <strong>{run.run_name}</strong>
        <span>{run.status} · {run.trade_date}</span>
      </div>
      <div className="progress-track" aria-label="运行进度">
        <i style={{ width: `${pct}%` }} />
      </div>
      <small>{run.progress_message || `推荐 ${run.recommended_count} / 股票池 ${run.universe_count}`}</small>
    </div>
  );
}

function TaskRunStrip({ rows, loading }: { rows: ScheduledTaskRun[]; loading: boolean }) {
  return (
    <div className="task-run-strip">
      {loading ? <span>任务执行记录加载中</span> : null}
      {rows.slice(0, 4).map((row) => (
        <span key={row.task_run_id}>
          <strong>{row.task_name}</strong>
          {taskLabel(row.task_type)} · {row.status}
        </span>
      ))}
      {!loading && !rows.length ? <span>暂无任务执行记录</span> : null}
    </div>
  );
}

function makeTaskId(draft: TaskDraft) {
  const suffix = `${draft.task_type}_${draft.trigger_time || "time"}_${draft.preset_id || "pool"}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 52);
  return `task_${suffix}_${Date.now().toString(36)}`;
}

function taskToPayload(task: ScheduledTask): ScheduledTaskPayload {
  return {
    task_id: task.task_id,
    name: task.name,
    task_type: task.task_type,
    trigger_time: task.trigger_time ?? cronToClockTime(task.cron_expression) ?? "09:05",
    payload: { ...(task.payload ?? {}) },
    enabled: task.enabled,
  };
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
  if (task.task_type === "recommendation_run") {
    const presetId = String(task.payload?.preset_id ?? "");
    return presets.find((preset) => preset.preset_id === presetId)?.name ?? (presetId || "未选择预设");
  }
  if (task.task_type === "pool_refresh") return "刷新统一报价池";
  if (task.task_type === "data_backfill") return "回填日K与辅助数据";
  return "行情 + 日K同步";
}

function taskLabel(type: ScheduledTaskType) {
  const labels: Record<ScheduledTaskType, string> = {
    recommendation_run: "股票推荐",
    data_sync: "数据同步",
    pool_refresh: "行情池刷新",
    data_backfill: "数据回填",
  };
  return labels[type];
}

function routeKeyLabel(route: ModelRoute) {
  if (route.uses_default_key && route.default_key_available) return "系统默认 Key";
  if (route.key_source === "custom") return route.api_key_masked || "自定义 Key";
  if (route.has_api_key) return route.api_key_masked || "可用 Key";
  return "未配置 Key";
}

function buildPresetDraft(sourcePreset: Preset | null, presetCount: number): PresetDraft {
  if (!sourcePreset) {
    return {
      ...EMPTY_PRESET_DRAFT,
      name: `预设 ${presetCount + 1}`,
    };
  }
  return {
    name: sourcePreset.name,
    description: sourcePreset.description ?? "",
    run_mode: sourcePreset.run_mode,
    market_scope: sourcePreset.market_scope,
    market_scope_value: sourcePreset.market_scope_value ?? "",
    risk_template: sourcePreset.risk_template,
    score_line: sourcePreset.score_line,
    data_source: normalizeManualRunDataSource(sourcePreset.data_source),
    hide_recent_repeats: sourcePreset.hide_recent_repeats,
  };
}

function buildPresetSkillDrafts(sourcePreset: Preset | null): PresetSkillDraft[] {
  if (!sourcePreset) return [];
  return normalizePresetSkillDrafts(sourcePreset.skills.map((skill, index) => ({
    skill_id: skill.skill_id,
    weight: skill.weight,
    sort_order: Number.isFinite(skill.sort_order) ? skill.sort_order : index + 1,
  })));
}

function normalizePresetSkillDrafts(rows: PresetSkillDraft[]): PresetSkillDraft[] {
  return rows
    .filter((row) => Boolean(row.skill_id))
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((row, index) => ({
      ...row,
      weight: Number.isFinite(row.weight) && row.weight > 0 ? row.weight : 1,
      sort_order: index + 1,
    }));
}

function normalizeManualRunDataSource(value: string | null | undefined): ManualRunDataSource {
  if (value === "local_pool_preferred" || value === "realtime") return value;
  return "local_pool";
}

function describePresetScope(preset: Preset) {
  const scopeLabel = labelText(preset.market_scope);
  return preset.market_scope_value ? `${scopeLabel} · ${preset.market_scope_value}` : scopeLabel;
}

function progressPercent(run: RunSummary) {
  if (typeof run.progress_pct === "number") {
    return Math.max(0, Math.min(100, run.progress_pct * 100));
  }
  if (!run.progress_total) return run.status === "completed" ? 100 : 0;
  return Math.max(0, Math.min(100, ((run.progress_current || 0) / run.progress_total) * 100));
}

function labelText(value: string) {
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
  return labels[value] ?? value;
}

function messageFromError(err: unknown) {
  if (err instanceof Error) return err.message === "UNAUTHORIZED" ? "请先登录" : err.message;
  return "操作失败";
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
