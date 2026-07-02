import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
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
import { businessStatusLabel, readinessCopy } from "../../utils/display";
import {
  EMPTY_PRESET_DRAFT,
  DEFAULT_RECOMMENDATION_DATA_SOURCE,
  EMPTY_ROUTE_DRAFT,
  EMPTY_TASK_DRAFT,
  INITIAL_SECTION_LOADING,
  buildPresetDraft,
  buildPresetSkillDrafts,
  buildTaskCron,
  cronToClockTime,
  describePresetScope,
  labelText,
  makeTaskId,
  messageFromError,
  normalizePresetSkillDrafts,
  taskToPayload,
  type ManualRunDataSource,
  type PresetDraft,
  type PresetSkillDraft,
  type RouteDraft,
  type SectionLoadState,
  type TaskDraft,
} from "./controlCenterModel";
import type {
  LocalPoolStatus,
  ModelRoute,
  ModelRouteTestResult,
  Preset,
  ScheduledTask,
  ScheduledTaskPayload,
  ScheduledTaskRun,
  ScheduledTaskType,
  SkillSummary,
} from "../../types";
type ControlSectionId = "control-strategy" | "control-automation" | "control-model";
const CONTROL_SECTION_NAV: Array<{ id: ControlSectionId; label: string; note: string }> = [
  { id: "control-strategy", label: "策略配置", note: "预设 / Skill" },
  { id: "control-automation", label: "自动化", note: "任务 / 记录" },
  { id: "control-model", label: "模型接入", note: "路由 / Key" },
];

export default function ControlCenterPage({ refreshSignal = 0 }: { refreshSignal?: number }) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [taskRuns, setTaskRuns] = useState<ScheduledTaskRun[]>([]);
  const [modelRoutes, setModelRoutes] = useState<ModelRoute[]>([]);
  const [pool, setPool] = useState<LocalPoolStatus | null>(null);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [taskDraft, setTaskDraft] = useState<TaskDraft>(EMPTY_TASK_DRAFT);
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [routeDraft, setRouteDraft] = useState<RouteDraft>(EMPTY_ROUTE_DRAFT);
  const [editingRouteId, setEditingRouteId] = useState<string | null>(null);
  const [presetDraft, setPresetDraft] = useState<PresetDraft>(EMPTY_PRESET_DRAFT);
  const [presetSkillDrafts, setPresetSkillDrafts] = useState<PresetSkillDraft[]>([]);
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [sectionLoading, setSectionLoading] = useState<SectionLoadState>(INITIAL_SECTION_LOADING);
  const [savingTask, setSavingTask] = useState(false);
  const [savingRoute, setSavingRoute] = useState(false);
  const [testingRoute, setTestingRoute] = useState(false);
  const [routeTestResult, setRouteTestResult] = useState<ModelRouteTestResult | null>(null);
  const [savingPreset, setSavingPreset] = useState(false);
  const [pendingDangerAction, setPendingDangerAction] = useState<string | null>(null);
  const [activeControlSection, setActiveControlSection] = useState<ControlSectionId>("control-strategy");
  const [sectionErrors, setSectionErrors] = useState<Partial<Record<keyof SectionLoadState, string>>>({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const selectedPreset = useMemo(
    () => presets.find((preset) => preset.preset_id === selectedPresetId) ?? presets[0] ?? null,
    [presets, selectedPresetId],
  );
  const visibleTasks = useMemo(() => sortControlTasks(tasks), [tasks]);
  const visibleTaskRuns = useMemo(() => sortControlTaskRuns(taskRuns), [taskRuns]);
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

  function editTask(task: ScheduledTask) {
    setError("");
    setEditingTaskId(task.task_id);
    setTaskDraft({
      task_id: task.task_id,
      name: task.name,
      task_type: task.task_type,
      cron_expression: task.cron_expression,
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
      const cronExpression = buildTaskCron(taskDraft);
      const payload: ScheduledTaskPayload = {
        task_id: taskId,
        name: taskDraft.name.trim() || taskLabel(taskDraft.task_type),
        task_type: taskDraft.task_type,
        trigger_time: taskDraft.trigger_time || "09:05",
        ...(cronExpression ? { cron_expression: cronExpression } : {}),
        payload: taskPayloadForDraft(taskDraft),
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
    setMessage(`正在手动执行自动任务：${task.name}...`);
    try {
      await api.runScheduledTask(task.task_id);
      setMessage(`${task.name} 已提交手动执行`);
      await reloadTasks();
    } catch (err) {
      setError(messageFromError(err));
      setMessage("");
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
    setMessage(nextPreset ? `已选择预设：${nextPreset.name}` : "已清空预设选择");
  }

  return (
    <div className="workspace-grid control-center">
      <section className="section-hero">
        <div>
          <span>控制中心</span>
          <h2>策略、调度与模型门禁</h2>
          <p>集中维护预设、自动任务和模型路由；执行推荐与数据同步已迁移到对应业务页。</p>
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

      <nav className="control-section-nav" role="tablist" aria-label="控制中心二级分区">
        {CONTROL_SECTION_NAV.map((item) => (
          <button
            key={item.id}
            id={`${item.id}-tab`}
            type="button"
            role="tab"
            aria-selected={activeControlSection === item.id}
            aria-controls={item.id}
            className={activeControlSection === item.id ? "active" : ""}
            onClick={() => setActiveControlSection(item.id)}
          >
            <strong>{item.label}</strong>
            <span>{item.note}</span>
          </button>
        ))}
      </nav>

      <ControlSection id="control-strategy" title="策略配置" meta="预设管理 / Skill 组合" active={activeControlSection === "control-strategy"}>
        <div className="control-grid wide-left">
          <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="预设"
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
            kicker="编辑"
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
      </ControlSection>

      <ControlSection id="control-automation" title="自动化" meta="自动任务 / 最近执行" active={activeControlSection === "control-automation"}>
        <div className="control-grid wide-left">
          <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="任务"
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
            ) : visibleTasks.length ? visibleTasks.map((task) => {
              const deleteArmed = pendingDangerAction === `task:${task.task_id}`;
              return (
              <article key={task.task_id} className={`control-row ${editingTaskId === task.task_id ? "active" : ""}`}>
                <button type="button" className="row-main-button" onClick={() => editTask(task)}>
                  <strong>{task.name}</strong>
                  <span>{taskLabel(task.task_type)} · {formatTaskTime(task)} · {describeTaskPayload(task, presets)}</span>
                  {taskStatusHint(task) ? <small>{taskStatusHint(task)}</small> : null}
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
          <TaskRunStrip rows={visibleTaskRuns} loading={sectionLoading.taskRuns} />
        </section>

        <section className="cockpit-panel control-panel">
          <PanelHeader kicker="任务" title={editingTaskId ? "编辑任务" : "新建任务"} />
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
              <select value={taskDraft.task_type} onChange={(event) => setTaskDraft((current) => taskDraftWithType(current, event.target.value as ScheduledTaskType))}>
                <option value="recommendation_run">股票推荐</option>
                <option value="preset_matrix_run">预设矩阵</option>
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
                  <option value="">默认（第一个预设）</option>
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
      </ControlSection>

      <ControlSection id="control-model" title="模型接入" meta="模型路由 / Key 测试" active={activeControlSection === "control-model"}>
        <div className="control-grid wide-left">
          <section className="cockpit-panel control-panel">
          <PanelHeader
            kicker="模型"
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
                  <span>{route.model} · {route.base_url || "-"} · {routeKeyLabel(route)} · {routeStatusLabel(route, index)}</span>
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
            kicker="密钥"
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
      </ControlSection>
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

function ControlSection({
  id,
  title,
  meta,
  active,
  children,
}: {
  id: ControlSectionId;
  title: string;
  meta: string;
  active: boolean;
  children: ReactNode;
}) {
  if (!active) return null;
  return (
    <section
      id={id}
      className="control-section-group"
      role="tabpanel"
      aria-labelledby={`${id}-tab`}
    >
      <div className="control-section-heading">
        <div>
          <span>{meta}</span>
          <h3>{title}</h3>
        </div>
      </div>
      {children}
    </section>
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
    message.includes("部分同步完成") ||
    message.includes("空结果待重试")
  ) {
    return "warning";
  }
  return "positive";
}

function TaskRunStrip({ rows, loading }: { rows: ScheduledTaskRun[]; loading: boolean }) {
  const latestRuns = latestTaskRunsByTask(rows).slice(0, 6);
  return (
    <div className="task-run-strip">
      {loading ? <span>任务执行记录加载中</span> : null}
      {latestRuns.map((row) => (
        <span key={row.task_run_id}>
          <strong>{row.task_name}</strong>
          {taskLabel(row.task_type)} · {businessStatusLabel(row.status)}
        </span>
      ))}
      {!loading && !rows.length ? <span>暂无任务执行记录</span> : null}
    </div>
  );
}

function latestTaskRunsByTask(rows: ScheduledTaskRun[]) {
  const latest = new Map<string, ScheduledTaskRun>();
  for (const row of rows) {
    const current = latest.get(row.task_id);
    if (!current || taskRunStartedMs(row) > taskRunStartedMs(current)) {
      latest.set(row.task_id, row);
    }
  }
  return Array.from(latest.values());
}

function sortControlTasks(tasks: ScheduledTask[]) {
  return [...tasks].sort((a, b) => {
    const enabledDelta = Number(b.enabled) - Number(a.enabled);
    if (enabledDelta !== 0) return enabledDelta;
    const timeDelta = taskTimeMinutes(a) - taskTimeMinutes(b);
    if (timeDelta !== 0) return timeDelta;
    return a.task_id.localeCompare(b.task_id);
  });
}

function sortControlTaskRuns(rows: ScheduledTaskRun[]) {
  return [...rows].sort((a, b) => {
    const aPostclose = a.task_id.startsWith("postclose_");
    const bPostclose = b.task_id.startsWith("postclose_");
    if (aPostclose !== bPostclose) return Number(bPostclose) - Number(aPostclose);
    const startedDelta = taskRunStartedMs(b) - taskRunStartedMs(a);
    if (startedDelta !== 0) return startedDelta;
    return a.task_run_id.localeCompare(b.task_run_id);
  });
}

function taskRunStartedMs(row: ScheduledTaskRun) {
  const startedMs = Date.parse(row.started_at ?? "");
  return Number.isFinite(startedMs) ? startedMs : 0;
}

function taskTimeMinutes(task: ScheduledTask) {
  const time = task.trigger_time ?? cronToClockTime(task.cron_expression);
  if (!time) return Number.MAX_SAFE_INTEGER;
  const [hourText, minuteText] = time.split(":", 2);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return Number.MAX_SAFE_INTEGER;
  return hour * 60 + minute;
}

function formatTaskTime(task: ScheduledTask) {
  return task.trigger_time ?? cronToClockTime(task.cron_expression) ?? task.cron_expression;
}

function describeTaskPayload(task: ScheduledTask, presets: Preset[]) {
  if (task.task_type === "recommendation_run") {
    const presetId = String(task.payload?.preset_id ?? "");
    return presets.find((preset) => preset.preset_id === presetId)?.name ?? (presetId || "未选择预设");
  }
  if (task.task_type === "preset_matrix_run") return String(task.payload?.matrix_batch_name ?? task.payload?.run_name_prefix ?? "盘后全部启用预设");
  if (task.task_type === "pool_refresh") return "刷新统一报价池";
  if (task.task_type === "data_backfill") return "回填日K与辅助数据";
  return "行情 + 日K同步";
}

function taskStatusHint(task: ScheduledTask) {
  if (task.task_id === "postclose_tushare_data_sync" && !task.enabled) {
    return "历史旧任务/已替代：保留停用状态，新收盘后链路由报价刷新、日K批量、健康补数和辅助同步拆分承接。";
  }
  if (task.task_id === "post_sync_formal_recommendation" && task.last_status === "interrupted") {
    return "最近一次中断：可在推荐运行页或复盘历史中使用断点继续，也可手动重跑该任务。";
  }
  return "";
}

function taskLabel(type: ScheduledTaskType) {
  const labels: Record<ScheduledTaskType, string> = {
    recommendation_run: "股票推荐",
    preset_matrix_run: "预设矩阵",
    data_sync: "数据同步",
    pool_refresh: "行情池刷新",
    data_backfill: "数据回填",
  };
  return labels[type];
}

function taskPayloadForDraft(draft: TaskDraft) {
  if (draft.task_type === "recommendation_run") {
    return { preset_id: draft.preset_id, run_name: `${draft.name.trim() || "自动推荐"} · 自动推荐` };
  }
  if (draft.task_type === "preset_matrix_run") {
    const name = draft.name.trim() || "盘后预设矩阵";
    return {
      matrix_batch_name: name,
      run_name_prefix: name,
      data_source: DEFAULT_RECOMMENDATION_DATA_SOURCE,
    };
  }
  return {};
}

function taskDraftWithType(draft: TaskDraft, taskType: ScheduledTaskType): TaskDraft {
  if (taskType === draft.task_type) return draft;
  if (taskType === "preset_matrix_run" && draft.trigger_time === EMPTY_TASK_DRAFT.trigger_time) {
    return { ...draft, task_type: taskType, trigger_time: "15:30" };
  }
  if (draft.task_type === "preset_matrix_run" && draft.trigger_time === "15:30") {
    return { ...draft, task_type: taskType, trigger_time: EMPTY_TASK_DRAFT.trigger_time };
  }
  return { ...draft, task_type: taskType };
}

function routeKeyLabel(route: ModelRoute) {
  if (route.uses_default_key && route.default_key_available) return "系统默认 Key";
  if (route.key_source === "custom") return route.api_key_masked || "自定义 Key";
  if (route.has_api_key) return route.api_key_masked || "可用 Key";
  return "未配置 Key";
}

function routeStatusLabel(route: ModelRoute, index: number) {
  if (!route.enabled) return "停用，不参与调用";
  if (index === 0) return "主路由";
  return `备用 ${index + 1}`;
}

function summarizeRouteTestFailure(message: string) {
  const text = message.toLowerCase();
  if (text.includes("余额不足") || text.includes("额度已用完") || text.includes("insufficient balance")) return "余额不足：";
  if (text.includes("api key") || text.includes("无权限") || text.includes("401") || text.includes("403")) return "鉴权失败：";
  if (text.includes("模型") && text.includes("不可用")) return "模型不可用：";
  return "测试失败：";
}
