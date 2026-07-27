import { useEffect, useMemo, useState } from "react";
import {
  Clock,
  Loader2,
  Pencil,
  Plus,
  Play,
  RefreshCw,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { RISK_TEMPLATE_MAP, DATA_SOURCE_MAP } from "../utils/riskTemplates";
import type { Preset, ScheduledTask, ScheduledTaskType } from "../types";
import { CardSkeleton, EmptyState, ErrorPage } from "./StateViews";
import { Panel } from "./Panel";

/**
 * 调度任务管理 —— 配置页「调度任务」Tab。
 *
 * 系统已有 8 个每日定时任务（盘前安全网 / 尾盘推荐 / 收盘后数据同步链 /
 * 17:30 主力推荐 等），后端 CRUD + 立即触发 + 热重载 API 齐全，此前无前端入口。
 * 本组件把这些能力暴露为可编辑卡片列表。
 *
 * 功能：
 * - 列出全部调度任务（名称 / 类型 / cron / 启用 / 上次状态）
 * - 开关 enabled、立即触发、删除（二次确认）
 * - 编辑 / 新建：按 task_type 渲染结构化 payload 表单
 */

// ── 显示映射 ──────────────────────────────────────────────────────
const TASK_TYPE_LABEL: Record<ScheduledTaskType, string> = {
  recommendation_run: "推荐流水线",
  formal_safety_net: "安全网",
  preset_matrix_run: "预设矩阵",
  data_sync: "数据同步",
  pool_refresh: "报价刷新",
  data_backfill: "数据补数",
};

/** 下拉框渲染用的选项列表（不包含后端存储值 local_pool_realtime） */
const DATA_SOURCES = ["local_pool_preferred", "auto"];

const STATUS_LABEL: Record<string, string> = {
  running: "运行中",
  completed: "成功",
  failed: "失败",
  partial_completed: "部分成功",
  skipped: "跳过",
};

const STATUS_COLOR: Record<string, string> = {
  running: "var(--color-up)",
  completed: "var(--color-up)",
  failed: "var(--color-down)",
  partial_completed: "var(--color-warn, #e0a800)",
  skipped: "var(--muted)",
};

const ALL_TASK_TYPES: ScheduledTaskType[] = [
  "recommendation_run",
  "formal_safety_net",
  "preset_matrix_run",
  "data_sync",
  "pool_refresh",
  "data_backfill",
];

// ── cron ↔ HH:MM 互转 ────────────────────────────────────────────
// 现有任务均为 "M H * * mon-fri"（工作日）或 "M H * * *"（每日）。
// 工作日一律写 mon-fri：APScheduler 的 day_of_week 是 0=Monday，写数字 1-5 会被当成
// 周二到周六（周一漏跑、周六误跑）。历史遗留的 1-5 仍按工作日识别以正确回显。
function cronToTriggerTime(cron: string): { time: string; weekdaysOnly: boolean } {
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) return { time: "17:30", weekdaysOnly: false };
  const [minute, hour, , , dow] = parts;
  const mm = String(minute).padStart(2, "0");
  const hh = String(hour).padStart(2, "0");
  return { time: `${hh}:${mm}`, weekdaysOnly: dow.toLowerCase() === "mon-fri" || dow === "1-5" };
}

function triggerTimeToCron(time: string, weekdaysOnly: boolean): string {
  const [hh, mm] = time.split(":");
  const dow = weekdaysOnly ? "mon-fri" : "*";
  return `${Number(mm)} ${Number(hh)} * * ${dow}`;
}

// cron → 人类可读
function cronToHuman(cron: string): string {
  const { time, weekdaysOnly } = cronToTriggerTime(cron);
  return `${weekdaysOnly ? "工作日" : "每日"} ${time}`;
}

// ── payload 表单类型 ─────────────────────────────────────────────
type PayloadForm = {
  // recommendation_run
  preset_id?: string;
  run_name?: string;
  run_mode?: string;
  data_source?: string;
  risk_template?: string;
  score_line?: number;
  output_count?: number;
  skip_aux_refresh?: boolean;
  // preset_matrix_run
  matrix_batch_name?: string;
  run_name_prefix?: string;
  stop_on_error?: boolean;
  // data_sync / data_backfill
  sync_scope?: string;
  auxiliary_remediation_tables?: string[];
  auxiliary_capital_flow_code_limit?: number;
  auxiliary_post_diagnosis?: boolean;
  health_remediation_tables?: string[];
  health_daily_kbar_code_limit?: number;
  health_minute_kbar_code_limit?: number;
  health_capital_flow_code_limit?: number;
  health_post_diagnosis?: boolean;
};

type TaskForm = {
  task_id: string;
  name: string;
  task_type: ScheduledTaskType;
  trigger_time: string;
  weekdays_only: boolean;
  enabled: boolean;
  payload: PayloadForm;
};

const SYNC_SCOPES_BY_TYPE: Partial<Record<ScheduledTaskType, { value: string; label: string }[]>> = {
  data_sync: [
    { value: "all", label: "全量（报价+日K+健康+辅助）" },
    { value: "auxiliary", label: "仅辅助数据" },
  ],
  data_backfill: [
    { value: "daily_bars", label: "日K按日批量" },
    { value: "health_remediation", label: "健康补数" },
  ],
};

const REMEDIABLE_TABLES = [
  "daily_kbar",
  "minute_kbar",
  "index_daily",
  "limit_up_board",
  "capital_flow",
  "concept_sector",
  "dragon_tiger",
];

const AUX_TABLES = ["limit_up_board", "concept_sector", "dragon_tiger"];

function emptyPayload(taskType: ScheduledTaskType): PayloadForm {
  switch (taskType) {
    case "recommendation_run":
      return {
        preset_id: "",
        run_name: "",
        run_mode: "formal",
        data_source: "local_pool_preferred",
        risk_template: "balanced",
        score_line: 72,
        output_count: 6,
        skip_aux_refresh: true,
      };
    case "preset_matrix_run":
      return {
        matrix_batch_name: "盘后预设矩阵",
        run_name_prefix: "",
        data_source: "local_pool_preferred",
        stop_on_error: false,
      };
    case "data_sync":
      return { sync_scope: "all" };
    case "data_backfill":
      return { sync_scope: "daily_bars" };
    default:
      return {};
  }
}

function formFromTask(t: ScheduledTask): TaskForm {
  const { time, weekdaysOnly } = cronToTriggerTime(t.cron_expression);
  const payload = t.payload ?? {};
  return {
    task_id: t.task_id,
    name: t.name,
    task_type: t.task_type,
    trigger_time: time,
    weekdays_only: weekdaysOnly,
    enabled: t.enabled,
    payload: { ...emptyPayload(t.task_type), ...payload } as PayloadForm,
  };
}

function emptyForm(): TaskForm {
  return {
    task_id: "",
    name: "",
    task_type: "recommendation_run",
    trigger_time: "17:30",
    weekdays_only: true,
    enabled: true,
    payload: emptyPayload("recommendation_run"),
  };
}

// ── 组件 ──────────────────────────────────────────────────────────
export function SchedulerTasksSection() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<TaskForm | null>(null);
  const [editSaving, setEditSaving] = useState(false);

  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState<TaskForm | null>(null);
  const [createSaving, setCreateSaving] = useState(false);

  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const [ts, ps] = await Promise.all([api.scheduledTasks(), api.presets()]);
      setTasks(ts);
      setPresets(ps);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload();
  }, []);

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  }

  // ── 开关 enabled（轻量 PATCH，不开编辑态）──
  async function toggleEnabled(t: ScheduledTask) {
    setBusyId(t.task_id);
    try {
      await api.updateScheduledTask(t.task_id, {
        task_id: t.task_id,
        name: t.name,
        task_type: t.task_type,
        cron_expression: t.cron_expression,
        payload: t.payload,
        enabled: !t.enabled,
      });
      showToast(`已${!t.enabled ? "启用" : "停用"}：${t.name}`);
      await reload();
    } catch (e) {
      showToast(`操作失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  }

  // ── 立即触发 ──
  async function runNow(t: ScheduledTask) {
    setBusyId(t.task_id);
    try {
      await api.runScheduledTask(t.task_id);
      showToast(`已触发：${t.name}`);
      // 触发是异步的，延迟刷新拿最新状态
      setTimeout(() => reload(), 2000);
    } catch (e) {
      showToast(`触发失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  }

  // ── 删除 ──
  async function handleDelete(t: ScheduledTask) {
    if (confirmDeleteId !== t.task_id) {
      setConfirmDeleteId(t.task_id);
      setTimeout(() => setConfirmDeleteId(null), 4000);
      return;
    }
    setBusyId(t.task_id);
    try {
      await api.deleteScheduledTask(t.task_id);
      showToast("已删除");
      await reload();
    } catch (e) {
      showToast(`删除失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
      setConfirmDeleteId(null);
    }
  }

  // ── 编辑保存 ──
  function startEdit(t: ScheduledTask) {
    setEditForm(formFromTask(t));
    setEditingId(t.task_id);
  }

  async function handleUpdate() {
    if (!editForm) return;
    if (!editForm.name.trim()) {
      showToast("任务名不能为空");
      return;
    }
    setEditSaving(true);
    try {
      await api.updateScheduledTask(editForm.task_id, {
        task_id: editForm.task_id,
        name: editForm.name,
        task_type: editForm.task_type,
        cron_expression: triggerTimeToCron(editForm.trigger_time, editForm.weekdays_only),
        payload: buildPayload(editForm),
        enabled: editForm.enabled,
      });
      showToast("已保存");
      setEditingId(null);
      setEditForm(null);
      await reload();
    } catch (e) {
      showToast(`保存失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setEditSaving(false);
    }
  }

  // ── 新建 ──
  function startCreate() {
    setCreateForm(emptyForm());
    setCreating(true);
  }

  async function handleCreate() {
    if (!createForm) return;
    if (!createForm.name.trim()) {
      showToast("任务名不能为空");
      return;
    }
    if (createForm.task_type === "recommendation_run" && !createForm.payload.preset_id) {
      showToast("推荐任务必须选择预设");
      return;
    }
    setCreateSaving(true);
    try {
      const taskId =
        createForm.task_id.trim() ||
        `custom_${createForm.task_type}_${Date.now().toString(36)}`;
      await api.createScheduledTask({
        task_id: taskId,
        name: createForm.name,
        task_type: createForm.task_type,
        cron_expression: triggerTimeToCron(createForm.trigger_time, createForm.weekdays_only),
        payload: buildPayload(createForm),
        enabled: createForm.enabled,
      });
      showToast("已创建");
      setCreating(false);
      setCreateForm(null);
      await reload();
    } catch (e) {
      showToast(`创建失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCreateSaving(false);
    }
  }

  // task_type 切换时重置 payload 默认值
  function applyTaskTypeChange(form: TaskForm, newType: ScheduledTaskType): TaskForm {
    return { ...form, task_type: newType, payload: emptyPayload(newType) };
  }

  if (loading) return <CardSkeleton count={4} />;
  if (error) return <ErrorPage title="调度任务加载失败" message={error} onRetry={reload} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {/* 顶部操作栏 */}
      <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
        <button className="cfg-btn" onClick={reload}>
          <RefreshCw size={15} />
          刷新
        </button>
        <button className="cfg-btn cfg-btn--primary" onClick={startCreate} disabled={creating}>
          <Plus size={15} />
          新建调度任务
        </button>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginLeft: "auto" }}>
          共 {tasks.length} 个任务
        </span>
      </div>

      {/* 新建表单 */}
      {creating && createForm && (
        <Panel
          kicker="新建"
          title="新建调度任务"
          actions={
            <button
              className="cfg-btn"
              onClick={() => {
                setCreating(false);
                setCreateForm(null);
              }}
            >
              <X size={15} />
              取消
            </button>
          }
        >
          <TaskFormBody
            form={createForm}
            presets={presets}
            onChange={setCreateForm}
            onTaskTypeChange={(nt) => setCreateForm((f) => (f ? applyTaskTypeChange(f, nt) : f))}
            isNew
          />
          <div style={{ marginTop: "var(--space-4)" }}>
            <button className="cfg-btn cfg-btn--primary" onClick={handleCreate} disabled={createSaving}>
              {createSaving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
              {createSaving ? "创建中…" : "创建任务"}
            </button>
          </div>
        </Panel>
      )}

      {/* 任务卡片列表 */}
      {tasks.length === 0 && !creating && (
        <EmptyState
          icon={<Clock size={28} />}
          title="暂无调度任务"
          reason="点击右上角「新建调度任务」创建第一个"
          action={
            <button className="cfg-btn cfg-btn--primary" onClick={startCreate}>
              <Plus size={15} />
              新建调度任务
            </button>
          }
        />
      )}

      {tasks.map((t) => {
        const isEditing = editingId === t.task_id;
        const isBusy = busyId === t.task_id;
        const lastStatus = t.last_status ?? null;
        return (
          <Panel
            key={t.task_id}
            kicker={TASK_TYPE_LABEL[t.task_type]}
            title={
              <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
                {t.name}
                {t.is_running && (
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      padding: "2px 8px",
                      borderRadius: 999,
                      color: "var(--color-up)",
                      background: "color-mix(in srgb, var(--color-up) 16%, transparent)",
                    }}
                  >
                    运行中
                  </span>
                )}
              </span>
            }
            actions={
              <span
                style={{ marginLeft: "auto", display: "flex", gap: "var(--space-2)", alignItems: "center" }}
              >
                <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)" }}>
                  {cronToHuman(t.cron_expression)}
                </span>
                <label
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: "var(--text-sm)",
                    color: t.enabled ? "var(--color-up)" : "var(--muted)",
                    cursor: isBusy ? "wait" : "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={t.enabled}
                    disabled={isBusy}
                    onChange={() => toggleEnabled(t)}
                    style={{ cursor: "pointer" }}
                  />
                  {t.enabled ? "启用" : "停用"}
                </label>
              </span>
            }
          >
            {/* 状态摘要 */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: "var(--space-3)",
                marginBottom: "var(--space-4)",
              }}
            >
              <StatusField label="任务 ID" value={t.task_id} mono />
              <StatusField label="cron 表达式" value={t.cron_expression} mono />
              <StatusField label="上次运行" value={formatTime(t.last_run_at)} />
              <div>
                <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginBottom: 4 }}>上次状态</div>
                {lastStatus ? (
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      fontWeight: 600,
                      padding: "2px 10px",
                      borderRadius: 999,
                      color: STATUS_COLOR[lastStatus] ?? "var(--muted)",
                      background: `color-mix(in srgb, ${STATUS_COLOR[lastStatus] ?? "var(--muted)"} 16%, transparent)`,
                    }}
                  >
                    {STATUS_LABEL[lastStatus] ?? lastStatus}
                  </span>
                ) : (
                  <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>未运行</span>
                )}
              </div>
            </div>

            {t.last_error && (
              <div
                style={{
                  marginBottom: "var(--space-4)",
                  padding: "var(--space-3)",
                  borderRadius: 8,
                  background: "color-mix(in srgb, var(--color-down) 8%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--color-down) 35%, transparent)",
                  color: "var(--color-down)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-sm)",
                  whiteSpace: "pre-wrap",
                }}
              >
                {t.last_error}
              </div>
            )}

            {/* 操作按钮 */}
            {!isEditing && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
                <button
                  className="cfg-btn cfg-btn--primary"
                  onClick={() => runNow(t)}
                  disabled={isBusy || t.is_running}
                >
                  {isBusy ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
                  立即触发
                </button>
                <button className="cfg-btn" onClick={() => startEdit(t)} disabled={isBusy}>
                  <Pencil size={15} />
                  编辑
                </button>
                <button
                  className="cfg-btn"
                  onClick={() => handleDelete(t)}
                  disabled={isBusy}
                  style={
                    confirmDeleteId === t.task_id
                      ? { borderColor: "var(--color-down)", color: "var(--color-down)" }
                      : undefined
                  }
                >
                  <Trash2 size={15} />
                  {confirmDeleteId === t.task_id ? "确认删除？" : "删除"}
                </button>
              </div>
            )}

            {/* 编辑表单 */}
            {isEditing && editForm && editForm.task_id === t.task_id && (
              <TaskFormBody
                form={editForm}
                presets={presets}
                onChange={setEditForm}
                onTaskTypeChange={undefined}
                isNew={false}
              />
            )}
            {isEditing && (
              <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-4)" }}>
                <button className="cfg-btn cfg-btn--primary" onClick={handleUpdate} disabled={editSaving}>
                  {editSaving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
                  {editSaving ? "保存中…" : "保存"}
                </button>
                <button
                  className="cfg-btn"
                  onClick={() => {
                    setEditingId(null);
                    setEditForm(null);
                  }}
                  disabled={editSaving}
                >
                  <X size={15} />
                  取消
                </button>
              </div>
            )}
          </Panel>
        );
      })}

      {/* toast */}
      {toast && (
        <div
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            padding: "var(--space-3) var(--space-4)",
            borderRadius: 10,
            background: "var(--surface-strong)",
            border: "1px solid var(--line-strong)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
            fontSize: "var(--text-sm)",
            zIndex: 1000,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}

// ── 表单主体（按 task_type 渲染）──────────────────────────────────
function TaskFormBody({
  form,
  presets,
  onChange,
  onTaskTypeChange,
  isNew,
}: {
  form: TaskForm;
  presets: Preset[];
  onChange: (f: TaskForm) => void;
  onTaskTypeChange?: (nt: ScheduledTaskType) => void;
  isNew: boolean;
}) {
  function setField<K extends keyof TaskForm>(key: K, value: TaskForm[K]) {
    onChange({ ...form, [key]: value });
  }
  function setPayload<K extends keyof PayloadForm>(key: K, value: PayloadForm[K]) {
    onChange({ ...form, payload: { ...form.payload, [key]: value } });
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
        gap: "var(--space-4)",
      }}
    >
      {/* 通用字段 */}
      <Field label="任务名" hint="显示用">
        <input
          className="cfg-input"
          value={form.name}
          onChange={(e) => setField("name", e.target.value)}
          placeholder="如：数据同步后正式推荐"
        />
      </Field>

      {isNew && (
        <Field label="任务类型" hint="决定 payload 字段">
          <select
            className="cfg-input"
            value={form.task_type}
            onChange={(e) => onTaskTypeChange?.(e.target.value as ScheduledTaskType)}
          >
            {ALL_TASK_TYPES.map((t) => (
              <option key={t} value={t}>
                {TASK_TYPE_LABEL[t]} ({t})
              </option>
            ))}
          </select>
        </Field>
      )}

      <Field label="触发时间" hint="HH:MM（24h）">
        <input
          className="cfg-input"
          type="time"
          value={form.trigger_time}
          onChange={(e) => setField("trigger_time", e.target.value)}
        />
      </Field>

      <Field label="触发周期">
        <select
          className="cfg-input"
          value={form.weekdays_only ? "1-5" : "*"}
          onChange={(e) => setField("weekdays_only", e.target.value === "1-5")}
        >
          <option value="1-5">工作日（周一至周五）</option>
          <option value="*">每日</option>
        </select>
      </Field>

      {/* payload 字段（按 task_type） */}
      {form.task_type === "recommendation_run" && (
        <>
          <Field label="预设" hint="必选">
            <select
              className="cfg-input"
              value={form.payload.preset_id ?? ""}
              onChange={(e) => setPayload("preset_id", e.target.value)}
            >
              <option value="">— 选择预设 —</option>
              {presets.map((p) => (
                <option key={p.preset_id} value={p.preset_id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="运行名称">
            <input
              className="cfg-input"
              value={form.payload.run_name ?? ""}
              onChange={(e) => setPayload("run_name", e.target.value)}
              placeholder="留空则自动生成"
            />
          </Field>
          <Field label="运行模式">
            <select
              className="cfg-input"
              value={form.payload.run_mode ?? "formal"}
              onChange={(e) => setPayload("run_mode", e.target.value)}
            >
              <option value="formal">formal（实盘）</option>
              <option value="backtest">backtest（回测）</option>
            </select>
          </Field>
          <Field label="数据源">
            <select
              className="cfg-input"
              value={form.payload.data_source ?? "auto"}
              onChange={(e) => setPayload("data_source", e.target.value)}
            >
              {DATA_SOURCES.map((v) => (
                <option key={v} value={v}>
                  {DATA_SOURCE_MAP[v]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="风险模板">
            <select
              className="cfg-input"
              value={form.payload.risk_template ?? "balanced"}
              onChange={(e) => setPayload("risk_template", e.target.value)}
            >
              {Object.entries(RISK_TEMPLATE_MAP).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="分数线" hint="0-100">
            <input
              className="cfg-input"
              type="number"
              min={0}
              max={100}
              value={form.payload.score_line ?? 72}
              onChange={(e) => setPayload("score_line", Number(e.target.value))}
            />
          </Field>
          <Field label="输出数量">
            <input
              className="cfg-input"
              type="number"
              min={1}
              value={form.payload.output_count ?? 6}
              onChange={(e) => setPayload("output_count", Number(e.target.value))}
            />
          </Field>
          <ToggleField
            label="跳过辅助数据刷新"
            hint="skip_aux_refresh"
            checked={form.payload.skip_aux_refresh ?? true}
            onChange={(v) => setPayload("skip_aux_refresh", v)}
          />
        </>
      )}

      {form.task_type === "preset_matrix_run" && (
        <>
          <Field label="矩阵批次名">
            <input
              className="cfg-input"
              value={form.payload.matrix_batch_name ?? ""}
              onChange={(e) => setPayload("matrix_batch_name", e.target.value)}
            />
          </Field>
          <Field label="运行名前缀">
            <input
              className="cfg-input"
              value={form.payload.run_name_prefix ?? ""}
              onChange={(e) => setPayload("run_name_prefix", e.target.value)}
              placeholder="留空则用批次名"
            />
          </Field>
          <Field label="数据源">
            <select
              className="cfg-input"
              value={form.payload.data_source ?? "auto"}
              onChange={(e) => setPayload("data_source", e.target.value)}
            >
              {DATA_SOURCES.map((v) => (
                <option key={v} value={v}>
                  {DATA_SOURCE_MAP[v]}
                </option>
              ))}
            </select>
          </Field>
          <ToggleField
            label="出错即停止"
            hint="stop_on_error"
            checked={form.payload.stop_on_error ?? false}
            onChange={(v) => setPayload("stop_on_error", v)}
          />
        </>
      )}

      {form.task_type === "data_sync" && (
        <>
          <Field label="同步范围">
            <select
              className="cfg-input"
              value={form.payload.sync_scope ?? "all"}
              onChange={(e) => setPayload("sync_scope", e.target.value)}
            >
              {SYNC_SCOPES_BY_TYPE.data_sync!.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          {form.payload.sync_scope === "auxiliary" && (
            <>
              <Field label="辅助表" hint="多选">
                <MultiSelect
                  options={AUX_TABLES}
                  selected={(form.payload.auxiliary_remediation_tables ?? []) as string[]}
                  onChange={(v) => setPayload("auxiliary_remediation_tables", v)}
                />
              </Field>
              <Field label="资金流股票上限">
                <input
                  className="cfg-input"
                  type="number"
                  min={1}
                  value={form.payload.auxiliary_capital_flow_code_limit ?? 300}
                  onChange={(e) => setPayload("auxiliary_capital_flow_code_limit", Number(e.target.value))}
                />
              </Field>
              <ToggleField
                label="补数后诊断"
                checked={form.payload.auxiliary_post_diagnosis ?? true}
                onChange={(v) => setPayload("auxiliary_post_diagnosis", v)}
              />
            </>
          )}
        </>
      )}

      {form.task_type === "data_backfill" && (
        <>
          <Field label="补数范围">
            <select
              className="cfg-input"
              value={form.payload.sync_scope ?? "daily_bars"}
              onChange={(e) => setPayload("sync_scope", e.target.value)}
            >
              {SYNC_SCOPES_BY_TYPE.data_backfill!.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          {form.payload.sync_scope === "health_remediation" && (
            <>
              <Field label="健康补数表" hint="多选">
                <MultiSelect
                  options={REMEDIABLE_TABLES}
                  selected={(form.payload.health_remediation_tables ?? []) as string[]}
                  onChange={(v) => setPayload("health_remediation_tables", v)}
                />
              </Field>
              <Field label="日K股票上限">
                <input
                  className="cfg-input"
                  type="number"
                  min={1}
                  value={form.payload.health_daily_kbar_code_limit ?? 6000}
                  onChange={(e) => setPayload("health_daily_kbar_code_limit", Number(e.target.value))}
                />
              </Field>
              <Field label="分钟K股票上限">
                <input
                  className="cfg-input"
                  type="number"
                  min={1}
                  value={form.payload.health_minute_kbar_code_limit ?? 300}
                  onChange={(e) => setPayload("health_minute_kbar_code_limit", Number(e.target.value))}
                />
              </Field>
              <Field label="资金流股票上限">
                <input
                  className="cfg-input"
                  type="number"
                  min={1}
                  value={form.payload.health_capital_flow_code_limit ?? 300}
                  onChange={(e) => setPayload("health_capital_flow_code_limit", Number(e.target.value))}
                />
              </Field>
              <ToggleField
                label="补数后诊断"
                checked={form.payload.health_post_diagnosis ?? true}
                onChange={(v) => setPayload("health_post_diagnosis", v)}
              />
            </>
          )}
        </>
      )}

      {form.task_type === "formal_safety_net" && (
        <div
          style={{
            gridColumn: "1 / -1",
            padding: "var(--space-3)",
            borderRadius: 8,
            background: "var(--surface-soft)",
            color: "var(--muted)",
            fontSize: "var(--text-sm)",
            lineHeight: 1.6,
          }}
        >
          安全网任务无独立 payload，触发时自动复用 <code>post_sync_formal_recommendation</code> 的配置。
          检测最近交易日若无成功 formal 推荐则自动补跑（最多 {3} 次）。
        </div>
      )}

      {form.task_type === "pool_refresh" && (
        <div
          style={{
            gridColumn: "1 / -1",
            padding: "var(--space-3)",
            borderRadius: 8,
            background: "var(--surface-soft)",
            color: "var(--muted)",
            fontSize: "var(--text-sm)",
          }}
        >
          报价刷新固定 sync_scope=quotes，无额外参数。
        </div>
      )}

      {/* 启用开关（新建/编辑都可改）*/}
      <ToggleField
        label="启用"
        checked={form.enabled}
        onChange={(v) => setField("enabled", v)}
      />
    </div>
  );
}

// ── 提交前清理 payload：剔除 undefined / 空值，保持后端存储干净 ──
function buildPayload(form: TaskForm): Record<string, unknown> {
  const p = form.payload;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v) && v.length === 0) continue;
    out[k] = v;
  }
  return out;
}

// ── 小组件 ────────────────────────────────────────────────────────
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "block" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 6,
          color: "var(--text-secondary)",
          fontSize: "var(--text-sm)",
        }}
      >
        <span style={{ fontWeight: 600 }}>{label}</span>
        {hint && <span style={{ color: "var(--muted)", fontWeight: 400 }}>· {hint}</span>}
      </div>
      {children}
    </label>
  );
}

function ToggleField({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        color: "var(--text-secondary)",
        fontSize: "var(--text-sm)",
        cursor: "pointer",
      }}
    >
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span style={{ fontWeight: 600 }}>{label}</span>
      {hint && <span style={{ color: "var(--muted)", fontWeight: 400 }}>· {hint}</span>}
    </label>
  );
}

function MultiSelect({
  options,
  selected,
  onChange,
}: {
  options: string[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
      {options.map((opt) => {
        const on = selected.includes(opt);
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(on ? selected.filter((x) => x !== opt) : [...selected, opt])}
            className="cfg-btn"
            style={
              on
                ? {
                    borderColor: "var(--accent)",
                    background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                    color: "var(--accent)",
                  }
                : undefined
            }
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}

function StatusField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: "var(--text-sm)", fontFamily: mono ? "var(--font-mono)" : undefined }}>{value}</div>
    </div>
  );
}

function formatTime(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s.endsWith("Z") ? s : s.replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString("zh-CN", { hour12: false });
}
