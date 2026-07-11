import { useEffect, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  KeyRound,
  Loader2,
  Pencil,
  Plug,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import type { ModelRoute, ModelRouteTestResult } from "../types";

/**
 * 模型路由管理 —— 配置页第二段。
 * 对应"调整优先级 + 点击测试 + 配置 key/api"：
 * - 列出全部模型路由（含优先级、启用状态、Key 来源）
 * - 上移/下移调整优先级（调用 reorderModelRoutes）
 * - 逐条「测试连接」（调用 testModelRoute）
 * - 新增 / 删除路由（含 base_url / api_key / model）
 * 这些路由是 Skill Agent 实际调用的 LLM 故障转移通道。
 */
export function ModelRoutesSection({ refreshKey }: { refreshKey?: number } = {}) {
  const [routes, setRoutes] = useState<ModelRoute[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [busyId, setBusyId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ModelRouteTestResult>>({});

  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    name: "",
    provider: "openai_compatible",
    base_url: "",
    model: "",
    api_key: "",
    enabled: true,
    max_tokens: "",
    temperature: "",
    timeout_seconds: "",
  });
  const [saving, setSaving] = useState(false);

  // 编辑已有路由
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({
    name: "",
    provider: "openai_compatible",
    base_url: "",
    model: "",
    api_key: "",
    enabled: true,
    clear_key: false,
    max_tokens: "",
    temperature: "",
    timeout_seconds: "",
  });
  const [editSaving, setEditSaving] = useState(false);

  function startEdit(route: ModelRoute) {
    setEditForm({
      name: route.name,
      provider: route.provider,
      base_url: route.base_url,
      model: route.model,
      api_key: "",
      enabled: route.enabled,
      clear_key: false,
      max_tokens: route.max_tokens != null ? String(route.max_tokens) : "",
      temperature: route.temperature != null ? String(route.temperature) : "",
      timeout_seconds: route.timeout_seconds != null ? String(route.timeout_seconds) : "",
    });
    setEditingId(route.route_id);
    setError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditForm({ name: "", provider: "openai_compatible", base_url: "", model: "", api_key: "", enabled: true, clear_key: false, max_tokens: "", temperature: "", timeout_seconds: "" });
  }

  async function handleEditSave(route: ModelRoute) {
    if (!editForm.name.trim() || !editForm.base_url.trim() || !editForm.model.trim()) {
      setError("名称、Base URL、模型 为必填");
      return;
    }
    setEditSaving(true);
    setError(null);
    const payload: Record<string, unknown> = {
      name: editForm.name.trim(),
      provider: editForm.provider.trim() || "openai_compatible",
      base_url: editForm.base_url.trim(),
      model: editForm.model.trim(),
      enabled: editForm.enabled,
    };
    // 编辑 Key 语义：勾选「清除」则清空该路由独立 Key（清空后此路由无 Key、变为不可用）；
    // 输入了新 Key 则更新；两者皆无则保留原 Key（不传 api_key 字段）。
    if (editForm.clear_key) {
      payload.clear_api_key = true;
    } else if (editForm.api_key.trim()) {
      payload.api_key = editForm.api_key.trim();
    }
    // 高级配置参数（为空表示使用系统默认值）
    if (editForm.max_tokens.trim()) {
      payload.max_tokens = Number(editForm.max_tokens);
    }
    if (editForm.temperature.trim()) {
      payload.temperature = Number(editForm.temperature);
    }
    if (editForm.timeout_seconds.trim()) {
      payload.timeout_seconds = Number(editForm.timeout_seconds);
    }
    try {
      await api.updateModelRoute(route.route_id, payload);
      setEditingId(null);
      setEditForm({ name: "", provider: "openai_compatible", base_url: "", model: "", api_key: "", enabled: true, clear_key: false, max_tokens: "", temperature: "", timeout_seconds: "" });
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditSaving(false);
    }
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.modelRoutes();
      setRoutes([...data].sort((a, b) => a.priority - b.priority));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  // 首次挂载及 refreshKey 变化时重新加载
  useEffect(() => {
    load();
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleTest(route: ModelRoute) {
    setBusyId(route.route_id);
    try {
      const res = await api.testModelRoute({ route_id: route.route_id });
      setTestResults((m) => ({ ...m, [route.route_id]: res }));
    } catch (e: unknown) {
      setTestResults((m) => ({
        ...m,
        [route.route_id]: { ok: false, message: e instanceof Error ? e.message : String(e) },
      }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleToggle(route: ModelRoute) {
    setBusyId(route.route_id);
    try {
      await api.updateModelRoute(route.route_id, { enabled: !route.enabled });
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(route: ModelRoute) {
    if (!confirm(`确认删除路由「${route.name}」？`)) return;
    setBusyId(route.route_id);
    try {
      await api.deleteModelRoute(route.route_id);
      setTestResults((m) => {
        const copy = { ...m };
        delete copy[route.route_id];
        return copy;
      });
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function handleMove(route: ModelRoute, dir: -1 | 1) {
    const idx = routes.findIndex((r) => r.route_id === route.route_id);
    const target = idx + dir;
    if (target < 0 || target >= routes.length) return;
    const next = [...routes];
    [next[idx], next[target]] = [next[target], next[idx]];
    setRoutes(next); // 乐观更新
    setBusyId("reorder");
    try {
      await api.reorderModelRoutes(next.map((r) => r.route_id));
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      await load();
    } finally {
      setBusyId(null);
    }
  }

  async function handleAdd() {
    if (!form.name.trim() || !form.base_url.trim() || !form.model.trim()) {
      setError("名称、Base URL、模型 为必填");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.createModelRoute({
        name: form.name.trim(),
        provider: form.provider.trim() || "openai_compatible",
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        api_key: form.api_key.trim() || undefined,
        enabled: form.enabled,
        ...(form.max_tokens.trim() ? { max_tokens: Number(form.max_tokens) } : {}),
        ...(form.temperature.trim() ? { temperature: Number(form.temperature) } : {}),
        ...(form.timeout_seconds.trim() ? { timeout_seconds: Number(form.timeout_seconds) } : {}),
      });
      setForm({ name: "", provider: "openai_compatible", base_url: "", model: "", api_key: "", enabled: true, max_tokens: "", temperature: "", timeout_seconds: "" });
      setAdding(false);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="panel" style={{ marginTop: "var(--space-6)" }}>
        <div className="panel__header">
          <span className="panel__kicker">故障转移</span>
          <h3 className="panel__title" style={{ fontSize: "var(--text-xl)" }}>模型路由</h3>
        </div>
        <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", padding: "var(--space-4)" }}>
          加载中…
        </div>
      </div>
    );
  }

  return (
    <div className="panel" style={{ marginTop: "var(--space-6)" }}>
      <div className="panel__header">
        <div>
          <span className="panel__kicker">故障转移</span>
          <h3 className="panel__title" style={{ fontSize: "var(--text-xl)" }}>模型路由</h3>
        </div>
        <button className="cfg-btn cfg-btn--primary" onClick={() => setAdding((v) => !v)} disabled={busyId === "reorder"}>
          <Plus size={15} />
          新增路由
        </button>
      </div>

      <p style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)", marginTop: "calc(-1 * var(--space-2))", marginBottom: "var(--space-3)", lineHeight: 1.6 }}>
        Skill Agent 实际调用的 LLM 通道，按优先级故障转移。可逐条测试连通性、调整优先级、配置独立 Key。
      </p>

      {error && (
        <div style={{ marginBottom: "var(--space-3)", padding: "var(--space-3)", borderRadius: 8, fontSize: "var(--text-sm)", background: "color-mix(in srgb, var(--color-down) 14%, transparent)", color: "var(--color-down)" }}>
          {error}
        </div>
      )}

      {adding && (
        <div
          style={{
            marginBottom: "var(--space-4)",
            padding: "var(--space-4)",
            borderRadius: 10,
            border: "1px solid var(--line)",
            background: "var(--surface-soft)",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "var(--space-3)",
          }}
        >
          <LabeledInput label="名称" value={form.name} placeholder="例如 DeepSeek" onChange={(v) => setForm((f) => ({ ...f, name: v }))} />
          <LabeledInput label="Provider" value={form.provider} placeholder="openai_compatible" onChange={(v) => setForm((f) => ({ ...f, provider: v }))} />
          <LabeledInput label="Base URL" value={form.base_url} placeholder="https://api.deepseek.com" onChange={(v) => setForm((f) => ({ ...f, base_url: v }))} />
          <LabeledInput label="模型" value={form.model} placeholder="deepseek-chat" onChange={(v) => setForm((f) => ({ ...f, model: v }))} />
          <LabeledInput label="API Key" value={form.api_key} placeholder="必填：本路由独立 Key（无默认 Key）" password onChange={(v) => setForm((f) => ({ ...f, api_key: v }))} />
          <LabeledInput label="Max Tokens" hint="留空使用系统默认" value={form.max_tokens} placeholder="如 40000" type="number" min={1} step={1} onChange={(v) => setForm((f) => ({ ...f, max_tokens: v }))} />
          <LabeledInput label="Temperature" hint="0-2，留空使用系统默认" value={form.temperature} placeholder="如 0.3" type="number" min={0} max={2} step={0.1} onChange={(v) => setForm((f) => ({ ...f, temperature: v }))} />
          <LabeledInput label="超时（秒）" hint="留空使用系统默认" value={form.timeout_seconds} placeholder="如 300" type="number" min={1} step={1} onChange={(v) => setForm((f) => ({ ...f, timeout_seconds: v }))} />
          <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--space-2)" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={form.enabled} onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))} />
              启用
            </label>
            <button className="cfg-btn cfg-btn--primary" onClick={handleAdd} disabled={saving}>
              {saving ? <Loader2 size={15} className="spin" /> : <Plus size={15} />}
              创建
            </button>
          </div>
        </div>
      )}

      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        {routes.map((r, i) => {
          const res = testResults[r.route_id];
          return (
            <div
              key={r.route_id}
              style={{
                padding: "var(--space-3) var(--space-4)",
                background: "var(--surface-1)",
                borderRadius: 8,
                border: "1px solid var(--line)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
                <span
                  style={{
                    minWidth: 26,
                    height: 26,
                    borderRadius: 999,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "var(--accent)",
                    color: "#fff",
                    fontSize: "var(--text-xs)",
                    fontWeight: 700,
                  }}
                  title="优先级"
                >
                  {r.priority}
                </span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 600 }}>{r.name}</div>
                  <div style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {r.model} · {r.provider}
                  </div>
                </div>

                <span
                  style={{
                    fontSize: "var(--text-xs)",
                    padding: "2px 10px",
                    borderRadius: 999,
                    color: r.enabled ? "var(--color-up)" : "var(--muted)",
                    background: r.enabled ? "color-mix(in srgb, var(--color-up) 14%, transparent)" : "var(--surface-2)",
                  }}
                >
                  {r.enabled ? "启用" : "停用"}
                </span>
                <span style={{ fontSize: "var(--text-xs)", color: r.has_api_key ? "var(--muted)" : "var(--color-down)" }}>
                  {r.api_key_masked || "未配置 Key（路由不可用）"}
                </span>

                <div style={{ display: "flex", gap: "var(--space-2)", marginLeft: "auto" }}>
                  <IconBtn title="编辑" onClick={() => startEdit(r)} disabled={busyId === r.route_id || busyId === "reorder" || editingId !== null}>
                    <Pencil size={14} />
                  </IconBtn>
                  <IconBtn title="测试连接" onClick={() => handleTest(r)} disabled={busyId === r.route_id || busyId === "reorder" || editingId === r.route_id}>
                    {busyId === r.route_id ? <Loader2 size={14} className="spin" /> : <Plug size={14} />}
                  </IconBtn>
                  <IconBtn title="上移（提高优先级）" onClick={() => handleMove(r, -1)} disabled={i === 0 || busyId === "reorder" || editingId !== null}>
                    <ArrowUp size={14} />
                  </IconBtn>
                  <IconBtn title="下移（降低优先级）" onClick={() => handleMove(r, 1)} disabled={i === routes.length - 1 || busyId === "reorder" || editingId !== null}>
                    <ArrowDown size={14} />
                  </IconBtn>
                  <IconBtn title="删除" onClick={() => handleDelete(r)} disabled={busyId === r.route_id || busyId === "reorder" || editingId === r.route_id} danger>
                    <Trash2 size={14} />
                  </IconBtn>
                </div>
              </div>

              {editingId === r.route_id && (
                <div
                  style={{
                    marginTop: "var(--space-3)",
                    padding: "var(--space-4)",
                    borderRadius: 10,
                    border: "1px solid var(--accent)",
                    background: "var(--surface-soft)",
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: "var(--space-3)",
                  }}
                >
                  <LabeledInput label="名称" value={editForm.name} placeholder="例如 DeepSeek" onChange={(v) => setEditForm((f) => ({ ...f, name: v }))} />
                  <LabeledInput label="Provider" value={editForm.provider} placeholder="openai_compatible" onChange={(v) => setEditForm((f) => ({ ...f, provider: v }))} />
                  <LabeledInput label="Base URL" value={editForm.base_url} placeholder="https://api.deepseek.com" onChange={(v) => setEditForm((f) => ({ ...f, base_url: v }))} />
                  <LabeledInput label="模型" value={editForm.model} placeholder="deepseek-chat" onChange={(v) => setEditForm((f) => ({ ...f, model: v }))} />
                  <LabeledInput
                    label="API Key"
                    value={editForm.api_key}
                    placeholder={r.has_api_key ? "留空则保留当前 Key" : "必填：请输入本路由独立 Key（无默认 Key）"}
                    password
                    onChange={(v) => setEditForm((f) => ({ ...f, api_key: v }))}
                  />
                  {/* 高级配置参数 */}
                  <LabeledInput
                    label="Max Tokens"
                    hint="单次最大输出，留空使用系统默认"
                    value={editForm.max_tokens}
                    placeholder="如 40000"
                    type="number"
                    min={1}
                    step={1}
                    onChange={(v) => setEditForm((f) => ({ ...f, max_tokens: v }))}
                  />
                  <LabeledInput
                    label="Temperature"
                    hint="0-2，越低越稳，留空使用系统默认"
                    value={editForm.temperature}
                    placeholder="如 0.3"
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    onChange={(v) => setEditForm((f) => ({ ...f, temperature: v }))}
                  />
                  <LabeledInput
                    label="超时（秒）"
                    hint="单次调用超时，留空使用系统默认"
                    value={editForm.timeout_seconds}
                    placeholder="如 300"
                    type="number"
                    min={1}
                    step={1}
                    onChange={(v) => setEditForm((f) => ({ ...f, timeout_seconds: v }))}
                  />
                  <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
                      <input type="checkbox" checked={editForm.enabled} onChange={(e) => setEditForm((f) => ({ ...f, enabled: e.target.checked }))} />
                      启用
                    </label>
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
                      <input type="checkbox" checked={editForm.clear_key} onChange={(e) => setEditForm((f) => ({ ...f, clear_key: e.target.checked }))} />
                      清除独立 Key
                    </label>
                    <div style={{ display: "flex", gap: "var(--space-2)", marginLeft: "auto" }}>
                      <button className="cfg-btn" onClick={cancelEdit} disabled={editSaving}>取消</button>
                      <button className="cfg-btn cfg-btn--primary" onClick={() => handleEditSave(r)} disabled={editSaving}>
                        {editSaving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
                        {editSaving ? "保存中…" : "保存修改"}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {res && (
                <div
                  style={{
                    marginTop: "var(--space-3)",
                    padding: "var(--space-2) var(--space-3)",
                    borderRadius: 6,
                    fontSize: "var(--text-sm)",
                    background: res.ok ? "color-mix(in srgb, var(--color-up) 8%, transparent)" : "color-mix(in srgb, var(--color-down) 8%, transparent)",
                    color: res.ok ? "var(--color-up)" : "var(--color-down)",
                    display: "flex",
                    gap: "var(--space-2)",
                    alignItems: "center",
                  }}
                >
                  <KeyRound size={13} />
                  {res.message}
                  {res.latency_ms != null && ` · ${res.latency_ms} ms`}
                </div>
              )}
            </div>
          );
        })}

        {routes.length === 0 && (
          <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", padding: "var(--space-4)", textAlign: "center" }}>
            暂无模型路由，点击「新增路由」添加。
          </div>
        )}
      </div>
    </div>
  );
}

function LabeledInput({
  label,
  value,
  placeholder,
  onChange,
  password,
  hint,
  type,
  min,
  max,
  step,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
  password?: boolean;
  hint?: string;
  type?: string;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label style={{ display: "block" }}>
      <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", marginBottom: 4, fontWeight: 600 }}>{label}</div>
      {hint ? (
        <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)", marginBottom: 4 }}>{hint}</div>
      ) : null}
      <input
        className="cfg-input"
        type={type ? (password ? "password" : type) : password ? "password" : "text"}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        min={min}
        max={max}
        step={step}
      />
    </label>
  );
}

function IconBtn({
  children,
  title,
  onClick,
  disabled,
  danger,
}: {
  children: React.ReactNode;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 30,
        height: 30,
        borderRadius: 8,
        border: "1px solid var(--line)",
        background: "var(--surface-soft)",
        color: danger ? "var(--color-down)" : "var(--text)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.4 : 1,
        transition: "all var(--motion-instant)",
      }}
    >
      {children}
    </button>
  );
}
