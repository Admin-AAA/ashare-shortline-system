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
  TestTube2,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import { businessStatusLabel, readinessCopy, runStatusLabel } from "../../utils/display";
import type {
  LocalPoolGapSyncResult,
  LocalPoolStatus,
  ModelRoute,
  ModelRouteTestResult,
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
const LOCAL_POOL_GAP_SYNC_CODE_LIMIT = 0;
const LOCAL_POOL_GAP_SYNC_DAYS = 90;
const LOCAL_POOL_GAP_SYNC_MIN_BARS = 30;
const INITIAL_SECTION_LOADING: SectionLoadState = {
  presets: true,
  skills: true,
  tasks: true,
  taskRuns: true,
  modelRoutes: true,
  pool: true,
};

export default function ControlCenterPage({ refreshSignal = 0 }: { refreshSignal?: number }) {
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
  const [testingRoute, setTestingRoute] = useState(false);
  const [routeTestResult, setRouteTestResult] = useState<ModelRouteTestResult | null>(null);
  const [savingPreset, setSavingPreset] = useState(false);
  const [pendingDangerAction, setPendingDangerAction] = useState<string | null>(null);
  const [sectionErrors, setSectionErrors] = useState<Partial<Record<keyof SectionLoadState, string>>>({});
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
  const poolReadinessCopy = readinessCopy(readiness?.can_run_local_recommendation, readiness?.status);
  const isRefreshingState = Object.values(sectionLoading).some(Boolean);

  function requireInlineDeleteGuard(actionKey: string) {
    if (pendingDangerAction === actionKey) {
      setPendingDangerAction(null);
      return true;
    }
    setPendingDangerAction(actionKey);
    setError("");
    setMessage("");
    return false;
  }

  const loadControlState = useCallback(async (showSuccess = false) => {
    setError("");
    if (showSuccess) setMessage("");
    setSectionErrors({});
    setSectionLoading(INITIAL_SECTION_LOADING);

    const errors: string[] = [];
    const nextSectionErrors: Partial<Record<keyof SectionLoadState, string>> = {};
    const finishSection = (section: keyof SectionLoadState) => {
      setSectionLoading((current) => ({ ...current, [section]: false }));
    };
    const captureError = (section: keyof SectionLoadState, label: string, err: unknown) => {
      const message = `${label}加载失败：${messageFromError(err)}`;
      nextSectionErrors[section] = message;
      errors.push(message);
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
          captureError("presets", "预设", err);
        } finally {
          finishSection("presets");
        }
      })(),
      (async () => {
        try {
          const skillRows = await api.skills();
          setSkills(skillRows.filter((skill) => skill.enabled));
        } catch (err) {
          captureError("skills", "策略", err);
        } finally {
          finishSection("skills");
        }
      })(),
      (async () => {
        try {
          setTasks(await api.scheduledTasks());
        } catch (err) {
          captureError("tasks", "自动任务", err);
        } finally {
          finishSection("tasks");
        }
      })(),
      (async () => {
        try {
          setTaskRuns(await api.scheduledTaskRuns(30));
        } catch (err) {
          captureError("taskRuns", "任务运行记录", err);
        } finally {
          finishSection("taskRuns");
        }
      })(),
      (async () => {
        try {
          setModelRoutes(await api.modelRoutes());
        } catch (err) {
          captureError("modelRoutes", "模型路由", err);
        } finally {
          finishSection("modelRoutes");
        }
      })(),
      (async () => {
        try {
          setPool(await api.localPool({ u_limit: 5, b_limit: 5, include_details: false }));
        } catch (err) {
          captureError("pool", "本地数据池", err);
        } finally {
          finishSection("pool");
        }
      })(),
    ]);
    setSectionErrors(nextSectionErrors);

    if (errors.length) {
      setError(errors.join("；"));
    } else if (showSuccess) {
      setMessage("控制中心状态已刷新");
    }
  }, []);

  useEffect(() => {
    void loadControlState(refreshSignal > 0);
  }, [loadControlState, refreshSignal]);

  async function runSelectedPreset() {
    if (!selectedPreset) {
      setError("请先选择一个推荐预设");
      setMessage("");
      return;
    }
    setRunning(true);
    setError("");
    setMessage(`正在触发推荐运行：${selectedPreset.name}...`);
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
        if (run.status === "completed") {
          setMessage(`推荐完成：输出 ${run.recommended_count} 只`);
          setError("");
        } else {
          const reason = run.error_message || run.progress_message || "请到运行页查看详情，并选择断点继续或重跑。";
          setError(`推荐${runStatusLabel(run.status)}：${reason}`);
          setMessage("");
        }
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
      setMessage("正在刷新统一报价池...");
      const quoteResult = await api.refreshLocalPool();

      setMessage(`报价池已刷新 ${quoteResult.refreshed} 条；正在全量检查候选池日K缺口...`);
      let gapSyncResult: LocalPoolGapSyncResult | null = null;
      try {
        gapSyncResult = await api.syncLocalPoolGaps({
          code_limit: LOCAL_POOL_GAP_SYNC_CODE_LIMIT,
          days: LOCAL_POOL_GAP_SYNC_DAYS,
          min_bars: LOCAL_POOL_GAP_SYNC_MIN_BARS,
          dry_run: false,
        });
      } catch (syncError) {
        const poolStatus = await api.localPool({ u_limit: 5, b_limit: 5, include_details: false });
        setPool(poolStatus);
        setError(
          `报价池已刷新 ${quoteResult.refreshed} 条，但候选股日K缺口同步失败：${messageFromError(syncError)}；`
          + `当前剩余缺口：${localPoolGapSummary(poolStatus)}`,
        );
        return;
      }

      setMessage(localPoolSyncSummary(quoteResult.refreshed, gapSyncResult));
      const poolStatus = await api.localPool({ u_limit: 5, b_limit: 5, include_details: false });
      setPool(poolStatus);
      setMessage(localPoolSyncSummary(quoteResult.refreshed, gapSyncResult, poolStatus));
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setSyncing(false);
    }
  }

  function editTask(task: ScheduledTask) {
    setError("");
    setEditingTaskId(task.task_id);
    setTaskDraft({
      task_id: task.task_id,
      name: task.name,
      task_type: task.task_type,
      trigger_time: task.trigger_time ?? cronToClockTime(task.cron_expression) ?? "09:05",
      preset_id: String(task.payload?.preset_id ?? selectedPresetId),
      enabled: task.enabled,
    });
    setMessage(`已载入任务：${task.name}`);
  }

  function resetTaskDraft() {
    setError("");
    setEditingTaskId(null);
    setTaskDraft({ ...EMPTY_TASK_DRAFT, preset_id: selectedPresetId || presets[0]?.preset_id || "" });
    setMessage("已切换到新建任务");
  }

  async function saveTask() {
    if (taskDraft.task_type === "recommendation_run" && !taskDraft.preset_id && taskDraft.enabled) {
      setError("推荐任务需要选择预设");
      setMessage("");
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
    setMessage(`正在${task.enabled ? "停用" : "启用"}自动任务：${task.name}...`);
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
    setMessage(`正在立即执行自动任务：${task.name}...`);
    try {
      await api.runScheduledTask(task.task_id);
      setMessage(`${task.name} 已立即执行`);
      await reloadTasks();
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function deleteTask(taskId: string) {
    if (!requireInlineDeleteGuard(`task:${taskId}`)) return;
    setError("");
    setMessage("");
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
    setError("");
    setEditingPresetId(null);
    setPresetDraft(buildPresetDraft(null, presets.length));
    setPresetSkillDrafts([]);
    setMessage("已切换到空白新增预设");
  }

  function resetPresetDraft() {
    setError("");
    if (editingPresetId) {
      const editingPreset = presets.find((preset) => preset.preset_id === editingPresetId) ?? null;
      setPresetDraft(buildPresetDraft(editingPreset, presets.length));
      setPresetSkillDrafts(buildPresetSkillDrafts(editingPreset));
      setMessage(editingPreset ? `已恢复编辑草稿：${editingPreset.name}` : "已恢复编辑草稿");
      return;
    }
    startBlankPreset();
  }

  function editPreset(preset: Preset) {
    setError("");
    setEditingPresetId(preset.preset_id);
    setSelectedPresetId(preset.preset_id);
    setSelectedRunDataSource(normalizeManualRunDataSource(preset.data_source));
    setPresetDraft(buildPresetDraft(preset, presets.length));
    setPresetSkillDrafts(buildPresetSkillDrafts(preset));
    setMessage(`已载入预设：${preset.name}`);
  }

  async function deletePreset(preset: Preset) {
    if (!requireInlineDeleteGuard(`preset:${preset.preset_id}`)) return;
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
      setMessage("");
      return;
    }
    if (!presetSkillDrafts.length) {
      setError("至少选择一个策略后才能保存预设");
      setMessage("");
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
    setError("");
    const selected = presetSkillDrafts.some((item) => item.skill_id === skill.skill_id);
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
    setMessage(selected ? `已移除策略：${skill.name}` : `已加入策略：${skill.name}`);
  }

  function updatePresetSkillWeight(skillId: string, weight: number) {
    setPresetSkillDrafts((current) => normalizePresetSkillDrafts(current.map((item) => (
      item.skill_id === skillId
        ? { ...item, weight: Number.isFinite(weight) && weight > 0 ? weight : item.weight }
        : item
    ))));
  }

  function startCreateRoute() {
    setError("");
    setEditingRouteId(null);
    setRouteTestResult(null);
    setRouteDraft({
      ...EMPTY_ROUTE_DRAFT,
      name: `模型路由 ${modelRoutes.length + 1}`,
      base_url: modelRoutes[0]?.base_url ?? "https://api.deepseek.com",
      model: modelRoutes[0]?.model ?? "deepseek-chat",
    });
    setMessage("已切换到新增模型路由");
  }

  function editRoute(route: ModelRoute) {
    setError("");
    setEditingRouteId(route.route_id);
    setRouteTestResult(null);
    setRouteDraft({
      name: route.name,
      provider: route.provider || "openai_compatible",
      base_url: route.base_url,
      model: route.model,
      api_key: "",
      enabled: route.enabled,
      clear_api_key: false,
    });
    setMessage(`已载入模型路由：${route.name}`);
  }

  function resetRouteDraft() {
    setError("");
    setEditingRouteId(null);
    setRouteTestResult(null);
    setRouteDraft(EMPTY_ROUTE_DRAFT);
    setMessage("模型路由表单已清空");
  }

  async function saveRoute() {
    if (!routeDraft.base_url.trim() || !routeDraft.model.trim()) {
      setError("模型路由需要接口地址和模型名");
      setMessage("");
      return;
    }
    setSavingRoute(true);
    setError("");
    setMessage("");
    setRouteTestResult(null);
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
    setMessage(`正在${route.enabled ? "停用" : "启用"}模型路由：${route.name}...`);
    try {
      await api.updateModelRoute(route.route_id, { enabled: !route.enabled });
      setModelRoutes(await api.modelRoutes());
      setMessage(`${route.name} ${route.enabled ? "已停用" : "已启用"}`);
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  async function testRoute() {
    if (!routeDraft.base_url.trim() || !routeDraft.model.trim()) {
      setError("模型路由需要接口地址和模型名");
      setMessage("");
      return;
    }
    setTestingRoute(true);
    setError("");
    setMessage("");
    setRouteTestResult(null);
    try {
      const payload: Record<string, unknown> = {
        route_id: editingRouteId ?? undefined,
        name: routeDraft.name.trim() || currentRoute?.name || "未命名模型路由",
        provider: routeDraft.provider || "openai_compatible",
        base_url: routeDraft.base_url.trim(),
        model: routeDraft.model.trim(),
        clear_api_key: routeDraft.clear_api_key,
      };
      if (routeDraft.api_key.trim()) payload.api_key = routeDraft.api_key.trim();
      const result = await api.testModelRoute(payload);
      if (result.ok) {
        const latency = typeof result.latency_ms === "number" ? `（${result.latency_ms}ms）` : "";
        setRouteTestResult({ ...result, message: `${result.message}${latency}` });
        return;
      }
      setRouteTestResult(result);
    } catch (err) {
      setRouteTestResult({ ok: false, message: messageFromError(err) });
    } finally {
      setTestingRoute(false);
    }
  }

  async function deleteRoute(routeId: string) {
    if (!requireInlineDeleteGuard(`route:${routeId}`)) return;
    setError("");
    setMessage("");
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
    setError("");
    setMessage("");
    try {
      setModelRoutes(await api.reorderModelRoutes(ordered.map((route) => route.route_id)));
      setMessage("模型路由顺序已更新");
    } catch (err) {
      setError(messageFromError(err));
    }
  }

  function handlePresetChange(presetId: string) {
    setError("");
    setSelectedPresetId(presetId);
    const nextPreset = presets.find((preset) => preset.preset_id === presetId);
    setSelectedRunDataSource((nextPreset?.data_source as ManualRunDataSource) || "local_pool");
    setMessage(nextPreset ? `已选择预设：${nextPreset.name}` : "已清空预设选择");
  }

  return (
    <div className="workspace-grid control-center">
      <section className="section-hero">
        <div>
          <span>控制中心</span>
          <h2>推荐、数据、调度与模型门禁</h2>
          <p>把影响短线推荐链路的操作集中在同一个驾驶舱入口，避免靠命令行触发关键流程。</p>
        </div>
        <button className="control-button" type="button" onClick={() => void loadControlState(true)} disabled={isRefreshingState}>
          {isRefreshingState ? <Loader2 size={15} className="spin" /> : <RefreshCcw size={15} />}
          {isRefreshingState ? "刷新中" : "刷新状态"}
        </button>
      </section>

      {error ? <ControlBanner tone="danger" message={error} /> : null}
      {message ? <ControlBanner tone={controlMessageTone(message)} message={message} /> : null}

      <div className="control-summary">
        <SummaryTile label="预设" value={presets.length} note={selectedPreset?.name ?? "未选择"} />
        <SummaryTile label="可用模型" value={usableRoutes} note={`${modelRoutes.length} 条路由`} />
        <SummaryTile label="启用任务" value={activeTasks} note={`${tasks.length} 个任务`} />
        <SummaryTile
          label="本地池"
          value={poolReadinessCopy.value}
          note={poolReadinessCopy.note}
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
                onChange={(event) => {
                  setError("");
                  setSelectedRunDataSource(event.target.value as ManualRunDataSource);
                  setMessage(`运行数据源已切换为：${labelText(event.target.value)}`);
                }}
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
          <div className="control-note">
            <DatabaseZap size={15} />
            <span>刷新会先更新统一报价池，再全量检查候选池并补齐可处理的日K缺口；下方只显示状态预览，不限制实际补齐范围。</span>
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
            {sectionErrors.presets ? (
              <div className="control-empty danger">{sectionErrors.presets}，请点击刷新状态重试。</div>
            ) : presets.length ? presets.map((preset) => {
              const deleteArmed = pendingDangerAction === `preset:${preset.preset_id}`;
              return (
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
                  <button
                    type="button"
                    className={`icon-button danger ${deleteArmed ? "armed" : ""}`}
                    title={deleteArmed ? "再点删除" : "删除"}
                    aria-label={deleteArmed ? "再点删除预设" : "删除预设"}
                    onClick={() => void deletePreset(preset)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                {deleteArmed ? (
                  <div className="row-danger-hint" role="status" aria-live="polite">
                    再点删除：预设 {preset.name}。
                  </div>
                ) : null}
              </article>
              );
            }) : <div className="control-empty">{sectionLoading.presets ? "预设加载中" : "暂无预设"}</div>}
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
              <button type="button" className={presetDraft.hide_recent_repeats ? "on" : ""} onClick={() => {
                setError("");
                setPresetDraft((current) => {
                  const next = !current.hide_recent_repeats;
                  setMessage(next ? "已开启隐藏近期重复票" : "已关闭隐藏近期重复票");
                  return { ...current, hide_recent_repeats: next };
                });
              }}>
                {presetDraft.hide_recent_repeats ? "ON" : "OFF"}
              </button>
            </label>
            <div className="control-form">
              <div className="panel-kicker">策略组合</div>
              {sectionLoading.skills ? <div className="control-empty">策略列表加载中</div> : null}
              {sectionErrors.skills ? (
                <div className="control-empty danger">{sectionErrors.skills}，请点击刷新状态重试。</div>
              ) : null}
              {!sectionLoading.skills && !sectionErrors.skills ? (
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
            {sectionErrors.tasks ? (
              <div className="control-empty danger">{sectionErrors.tasks}，请点击刷新状态重试。</div>
            ) : tasks.length ? tasks.map((task) => {
              const deleteArmed = pendingDangerAction === `task:${task.task_id}`;
              return (
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
                  <button
                    type="button"
                    className={`icon-button danger ${deleteArmed ? "armed" : ""}`}
                    title={deleteArmed ? "再点删除" : "删除"}
                    aria-label={deleteArmed ? "再点删除自动任务" : "删除自动任务"}
                    onClick={() => void deleteTask(task.task_id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                {deleteArmed ? (
                  <div className="row-danger-hint" role="status" aria-live="polite">
                    再点删除：自动任务 {task.name}。
                  </div>
                ) : null}
              </article>
              );
            }) : <div className="control-empty">{sectionLoading.tasks ? "自动任务加载中" : "暂无自动任务"}</div>}
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
              <button type="button" className={taskDraft.enabled ? "on" : ""} onClick={() => {
                setError("");
                setTaskDraft((current) => {
                  const next = !current.enabled;
                  setMessage(next ? "新任务将启用" : "新任务将保存为停用");
                  return { ...current, enabled: next };
                });
              }}>
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
            {sectionErrors.modelRoutes ? (
              <div className="control-empty danger">{sectionErrors.modelRoutes}，请点击刷新状态重试。</div>
            ) : modelRoutes.length ? modelRoutes.map((route, index) => {
              const deleteArmed = pendingDangerAction === `route:${route.route_id}`;
              return (
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
                  <button
                    type="button"
                    className={`icon-button danger ${deleteArmed ? "armed" : ""}`}
                    title={deleteArmed ? "再点删除" : "删除"}
                    aria-label={deleteArmed ? "再点删除模型路由" : "删除模型路由"}
                    onClick={() => void deleteRoute(route.route_id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                {deleteArmed ? (
                  <div className="row-danger-hint" role="status" aria-live="polite">
                    再点删除：模型路由 {route.name}。
                  </div>
                ) : null}
              </article>
              );
            }) : <div className="control-empty">{sectionLoading.modelRoutes ? "模型路由加载中" : "暂无模型路由"}</div>}
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
              <input value={routeDraft.name} onChange={(event) => {
                setRouteTestResult(null);
                setRouteDraft((current) => ({ ...current, name: event.target.value }));
              }} />
            </label>
            <label className="control-field">
              <span>接口地址</span>
              <input value={routeDraft.base_url} placeholder="https://api.deepseek.com" onChange={(event) => {
                setRouteTestResult(null);
                setRouteDraft((current) => ({ ...current, base_url: event.target.value }));
              }} />
            </label>
            <label className="control-field">
              <span>模型名</span>
              <input value={routeDraft.model} placeholder="deepseek-chat" onChange={(event) => {
                setRouteTestResult(null);
                setRouteDraft((current) => ({ ...current, model: event.target.value }));
              }} />
            </label>
            <label className="control-field">
              <span>{editingRouteId ? "新 API Key" : "API Key"}</span>
              <input type="password" value={routeDraft.api_key} placeholder={editingRouteId ? "留空表示不改" : "未填则走系统默认 Key"} onChange={(event) => {
                setRouteTestResult(null);
                setRouteDraft((current) => ({ ...current, api_key: event.target.value }));
              }} />
            </label>
            <label className="control-toggle">
              <span>启用路由</span>
              <button type="button" className={routeDraft.enabled ? "on" : ""} onClick={() => {
                setError("");
                setRouteTestResult(null);
                setRouteDraft((current) => {
                  const next = !current.enabled;
                  setMessage(next ? "模型路由草稿将启用" : "模型路由草稿将停用");
                  return { ...current, enabled: next };
                });
              }}>
                {routeDraft.enabled ? "ON" : "OFF"}
              </button>
            </label>
            {editingRouteId ? (
              <label className="control-toggle">
                <span>清空自定义 Key</span>
                <button type="button" className={routeDraft.clear_api_key ? "on" : ""} onClick={() => {
                  setError("");
                  setRouteTestResult(null);
                  setRouteDraft((current) => {
                    const next = !current.clear_api_key;
                    setMessage(next ? "保存时会清空自定义 Key" : "已取消清空自定义 Key");
                    return { ...current, clear_api_key: next };
                  });
                }}>
                  {routeDraft.clear_api_key ? "ON" : "OFF"}
                </button>
              </label>
            ) : null}
            <div className="control-note">
              <KeyRound size={14} />
              <span>{currentRoute ? `当前状态：${routeKeyLabel(currentRoute)}` : "未填写 API Key 时，会尝试使用系统默认 Key。"}</span>
            </div>
            <div className="control-actions">
              <button className="control-button wide" type="button" onClick={() => void testRoute()} disabled={testingRoute || savingRoute}>
                {testingRoute ? <Loader2 size={15} className="spin" /> : <TestTube2 size={15} />}
                测试连接
              </button>
              <button className="control-button primary wide" type="button" onClick={() => void saveRoute()} disabled={savingRoute || testingRoute}>
                {savingRoute ? <Loader2 size={15} className="spin" /> : <CheckCircle2 size={15} />}
                保存模型路由
              </button>
            </div>
            {routeTestResult ? (
              <div className={`control-note route-test-feedback ${routeTestResult.ok ? "positive" : "danger"}`}>
                <span><strong>{routeTestResult.ok ? "测试通过" : summarizeRouteTestFailure(routeTestResult.message)}</strong>{routeTestResult.message}</span>
              </div>
            ) : null}
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

function ControlBanner({ tone, message }: { tone: "positive" | "warning" | "danger"; message: string }) {
  const parts = message.split("；").map((item) => item.trim()).filter(Boolean);
  if (parts.length <= 1) {
    return <div className={`control-banner ${tone}`} role={tone === "danger" ? "alert" : "status"} aria-live="polite">{message}</div>;
  }
  const visibleParts = parts.slice(0, 4);
  const hiddenCount = parts.length - visibleParts.length;
  const title = tone === "danger" ? `${parts.length} 个区域加载失败，页面保留可操作表单` : parts[0];
  return (
    <div className={`control-banner ${tone} compact`} role={tone === "danger" ? "alert" : "status"} aria-live="polite">
      <strong>{title}</strong>
      <div>
        {visibleParts.slice(tone === "danger" ? 0 : 1).map((item) => <span key={item}>{item}</span>)}
        {hiddenCount > 0 ? <span>{tone === "danger" ? `另有 ${hiddenCount} 项失败，可刷新状态重试` : `另有 ${hiddenCount} 条状态详情`}</span> : null}
      </div>
    </div>
  );
}

function controlMessageTone(message: string): "positive" | "warning" {
  if (
    message.includes("部分同步完成")
  ) {
    return "warning";
  }
  return "positive";
}

function localPoolSyncSummary(
  refreshedQuotes: number,
  gapSync: LocalPoolGapSyncResult,
  poolStatus?: LocalPoolStatus,
) {
  const planned = gapSync.target_count ?? 0;
  const processed = gapSync.processed_count ?? 0;
  const completed = gapSync.succeeded_count ?? 0;
  const unresolved = gapSync.unresolved_count ?? 0;
  const failed = gapSync.failed_count ?? 0;
  const rows = gapSync.row_count ?? 0;
  const remaining = poolStatus ? localPoolGapSummary(poolStatus) : formatGapCounts(gapSync.after);
  const comparison = gapSync.comparison;
  const checked = gapSync.before?.checked_count ?? comparison?.before_candidate_count ?? 0;
  const candidateCount = gapSync.before?.candidate_count ?? comparison?.before_candidate_count ?? checked;
  const resolved = comparison?.resolved_target_count ?? Math.max(0, (gapSync.before?.gap_count ?? 0) - (gapSync.after?.gap_count ?? 0));
  const remainingGaps = comparison?.unresolved_target_count ?? gapSync.after?.gap_count ?? 0;
  const requestedCodeLimit = gapSync.code_limit ?? 0;
  const scopeText = requestedCodeLimit > 0
    ? `前 ${requestedCodeLimit} 只缺口目标`
    : "全量候选池";
  const hasRemainingGaps = (gapSync.after?.gap_count ?? 0) > 0 || unresolved > 0 || failed > 0;
  const parts = [
    `数据池${hasRemainingGaps ? "部分同步完成" : "同步完成"}：报价 ${refreshedQuotes} 条`,
    `同步范围：${scopeText}，已检查候选 ${checked}/${candidateCount} 只，可补日K缺口目标 ${planned} 只`,
    `候选股日K缺口 ${formatGapCounts(gapSync.before)} → ${formatGapCounts(gapSync.after)}`,
    `当前可补缺口已按本次范围处理：已处理 ${processed} 只，请求成功 ${completed} 只，实际修复 ${resolved} 只，剩余缺口 ${remainingGaps} 只，写入 ${rows} 行`,
  ];
  if (comparison) {
    parts.push(
      `候选覆盖率 ${formatRatioPercent(comparison.candidate_coverage_rate_before)} → ${formatRatioPercent(comparison.candidate_coverage_rate_after)}，修复率 ${formatRatioPercent(comparison.remediation_coverage_rate)}`,
    );
  }
  if (failed > 0) parts.push(`失败 ${failed} 只：${localPoolFailureSummary(gapSync)}`);
  parts.push(`当前剩余缺口：${remaining}`);
  return parts.join("；");
}

function localPoolFailureSummary(gapSync: LocalPoolGapSyncResult) {
  const reasons = gapSync.sync_summary?.failure_reasons ?? [];
  if (reasons.length === 0) return "暂无失败原因明细，请稍后重试或查看后端日志";
  return reasons
    .map((item) => {
      const count = Number(item.count ?? 0);
      const type = item.error_type ? `${item.error_type}: ` : "";
      const message = item.message || "未知错误";
      return `${count} 只 ${type}${message}`;
    })
    .join("；");
}

function formatGapCounts(counts?: LocalPoolGapSyncResult["before"]) {
  if (!counts) return "缺口未知";
  const total = Number(counts.gap_count ?? 0);
  const immatureHistory = Number(counts.immature_history_count ?? 0);
  if (total <= 0) {
    return immatureHistory > 0 ? `暂无可补缺口，短历史风险 ${immatureHistory} 只` : "暂无缺口";
  }
  const remediableShortHistory = Math.max(0, Number(counts.short_history_count ?? 0) - immatureHistory);
  const parts = [
    gapText("日K缺失", counts.missing_count),
    gapText("短历史", remediableShortHistory),
    gapText("日K过期", counts.stale_count),
    gapText("短历史风险", immatureHistory),
  ].filter(Boolean);
  return parts.length ? parts.join("，") : `缺口 ${total} 只`;
}

function localPoolGapSummary(poolStatus: LocalPoolStatus) {
  const kline = poolStatus.recommendation_readiness?.kline;
  if (kline) {
    const immatureHistory = Number(kline.immature_history_count ?? 0);
    const remediableShortHistory = Math.max(0, Number(kline.short_history_count ?? 0) - immatureHistory);
    const candidateGaps = [
      gapText("日K缺失", kline.missing_count),
      gapText("短历史", remediableShortHistory),
      gapText("日K过期", kline.stale_count),
    ].filter(Boolean);
    const risk = gapText("短历史风险", immatureHistory);
    if (candidateGaps.length) return [...candidateGaps, risk].filter(Boolean).join("，");
    return risk ? `暂无可补候选日K缺口，${risk}` : "暂无候选日K缺口";
  }

  const summary = poolStatus.summary;
  const gaps = [
    gapText("日K缺失", summary.daily_bar_missing_count),
    gapText("短历史", summary.daily_bar_short_history_count),
    gapText("日K过期", summary.daily_bar_stale_count),
  ].filter(Boolean);
  return gaps.length ? gaps.join("，") : "暂无候选日K缺口";
}

function gapText(label: string, value?: number) {
  const count = Number(value ?? 0);
  return count > 0 ? `${label} ${count} 只` : "";
}

function formatRatioPercent(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function RunStatusCard({ run }: { run: RunSummary }) {
  const pct = progressPercent(run);
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
          {taskLabel(row.task_type)} · {businessStatusLabel(row.status)}
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

function summarizeRouteTestFailure(message: string) {
  const text = message.toLowerCase();
  if (text.includes("余额不足") || text.includes("额度已用完") || text.includes("insufficient balance")) return "余额不足：";
  if (text.includes("api key") || text.includes("无权限") || text.includes("401") || text.includes("403")) return "鉴权失败：";
  if (text.includes("模型") && text.includes("不可用")) return "模型不可用：";
  return "测试失败：";
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
  return labels[value] ?? businessStatusLabel(value);
}

function messageFromError(err: unknown) {
  if (err instanceof Error) return err.message === "UNAUTHORIZED" ? "请先登录" : err.message;
  return "操作失败";
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
