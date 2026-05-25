import { memo, useMemo, useState, type ReactNode } from "react";
import {
  ArrowDown,
  ArrowUp,
  KeyRound,
  PauseCircle,
  Play,
  Plus,
  RefreshCcw,
  Save,
  SquarePen,
  Trash2,
  X,
} from "lucide-react";
import type { ModelRoute } from "../types";

type Draft = {
  name: string;
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
  enabled: boolean;
  clear_api_key: boolean;
};

type Props = {
  routes: ModelRoute[];
  onRefresh: () => void;
  onCreate: (payload: Record<string, unknown>) => Promise<void>;
  onUpdate: (routeId: string, payload: Record<string, unknown>) => Promise<void>;
  onDelete: (routeId: string) => Promise<void>;
  onMove: (routeId: string, direction: "up" | "down") => Promise<void>;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  provider: "openai_compatible",
  base_url: "",
  model: "",
  api_key: "",
  enabled: true,
  clear_api_key: false,
};

export default memo(function ModelRoutePanel({
  routes,
  onRefresh,
  onCreate,
  onUpdate,
  onDelete,
  onMove,
}: Props) {
  const [editingRouteId, setEditingRouteId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);

  const enabledCount = useMemo(() => routes.filter((item) => item.enabled).length, [routes]);
  const customKeyCount = useMemo(() => routes.filter((item) => item.key_source === "custom").length, [routes]);
  const availableCount = useMemo(() => routes.filter((item) => item.has_api_key).length, [routes]);
  const editingRoute = routes.find((item) => item.route_id === editingRouteId) ?? null;

  function resetDraft() {
    setEditingRouteId(null);
    setDraft(EMPTY_DRAFT);
  }

  function startCreate() {
    setEditingRouteId(null);
    setDraft({
      ...EMPTY_DRAFT,
      name: `模型路由 ${routes.length + 1}`,
      base_url: routes[0]?.base_url ?? "",
      model: routes[0]?.model ?? "",
    });
  }

  function startEdit(route: ModelRoute) {
    setEditingRouteId(route.route_id);
    setDraft({
      name: route.name,
      provider: route.provider || "openai_compatible",
      base_url: route.base_url,
      model: route.model,
      api_key: "",
      enabled: route.enabled,
      clear_api_key: false,
    });
  }

  async function saveDraft() {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        name: draft.name.trim() || "未命名模型",
        provider: draft.provider,
        base_url: draft.base_url.trim(),
        model: draft.model.trim(),
        enabled: draft.enabled,
      };
      if (draft.api_key.trim()) payload.api_key = draft.api_key.trim();
      if (draft.clear_api_key) payload.clear_api_key = true;

      if (editingRouteId) {
        await onUpdate(editingRouteId, payload);
      } else {
        await onCreate(payload);
      }
      resetDraft();
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(routeId: string) {
    if (!window.confirm("确定删除这条模型路由？")) return;
    await onDelete(routeId);
    if (editingRouteId === routeId) {
      resetDraft();
    }
  }

  return (
    <section className="panel">
      <PanelTitle
        title="模型路由"
        subtitle="正式推荐会按优先级依次尝试模型，当前路由失败时自动切到下一条"
        action={
          <div className="inline-actions">
            <button className="ghost-btn" onClick={onRefresh}>
              <RefreshCcw size={14} />
              刷新
            </button>
            <button className="ghost-btn primary" onClick={startCreate}>
              <Plus size={14} />
              新增路由
            </button>
          </div>
        }
      />

      <div className="summary-grid source-summary-grid">
        <MiniStat label="启用路由" value={enabledCount} />
        <MiniStat label="可用 Key" value={availableCount} />
        <MiniStat label="自定义 Key" value={customKeyCount} />
        <MiniStat label="总路由" value={routes.length} />
      </div>

      <div className="model-route-list">
        {routes.map((route, index) => (
          <div className={`model-route-row ${route.enabled ? "" : "disabled"}`} key={route.route_id}>
            <div className="model-route-main">
              <div className="model-route-title">
                <strong>{route.name}</strong>
                <Tag text={route.enabled ? "enabled" : "disabled"} />
                <span className="mini-chip">优先级 {route.priority}</span>
                <span className={`mini-chip ${route.has_api_key ? "" : "warning"}`}>{keyStatusLabel(route)}</span>
              </div>
              <div className="source-meta">
                <span>模型 {route.model}</span>
                <span>接口 {route.base_url || "-"}</span>
              </div>
            </div>
            <div className="model-route-actions">
              <button className="ghost-btn subtle" disabled={index === 0} onClick={() => void onMove(route.route_id, "up")}>
                <ArrowUp size={14} />
                上移
              </button>
              <button
                className="ghost-btn subtle"
                disabled={index === routes.length - 1}
                onClick={() => void onMove(route.route_id, "down")}
              >
                <ArrowDown size={14} />
                下移
              </button>
              <button className="ghost-btn" onClick={() => startEdit(route)}>
                <SquarePen size={14} />
                编辑
              </button>
              <button
                className={`ghost-btn ${route.enabled ? "subtle" : "primary"}`}
                onClick={() => void onUpdate(route.route_id, { enabled: !route.enabled })}
              >
                {route.enabled ? <PauseCircle size={14} /> : <Play size={14} />}
                {route.enabled ? "停用" : "启用"}
              </button>
              <button className="ghost-btn danger" onClick={() => void handleDelete(route.route_id)}>
                <Trash2 size={14} />
                删除
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="config-card model-route-form-card">
        <div className="config-card-head">
          <h3 className="config-card-title">{editingRoute ? `编辑 · ${editingRoute.name}` : "新增模型路由"}</h3>
          {(editingRouteId || draft.name || draft.base_url || draft.model || draft.api_key) ? (
            <button className="ghost-btn subtle" onClick={resetDraft}>
              <X size={14} />
              清空
            </button>
          ) : null}
        </div>
        <div className="form-grid">
          <Field label="名称" value={draft.name} onChange={(value) => setDraft((current) => ({ ...current, name: value }))} />
          <SelectField
            label="协议"
            value={draft.provider}
            options={[{ value: "openai_compatible", label: "OpenAI 兼容" }]}
            onChange={(value) => setDraft((current) => ({ ...current, provider: value }))}
          />
          <Field
            label="接口地址"
            value={draft.base_url}
            onChange={(value) => setDraft((current) => ({ ...current, base_url: value }))}
            placeholder="https://api.deepseek.com"
          />
          <Field
            label="模型名"
            value={draft.model}
            onChange={(value) => setDraft((current) => ({ ...current, model: value }))}
            placeholder="deepseek-v4-pro"
          />
          <PasswordField
            label={editingRoute ? "新 API Key" : "API Key"}
            value={draft.api_key}
            onChange={(value) => setDraft((current) => ({ ...current, api_key: value }))}
            placeholder={editingRoute ? "留空表示不改" : "未填则走系统默认 Key"}
          />
          <ToggleField
            label="启用"
            value={draft.enabled}
            onChange={(value) => setDraft((current) => ({ ...current, enabled: value }))}
          />
          {editingRoute ? (
            <ToggleField
              label="清空自定义 Key"
              value={draft.clear_api_key}
              onChange={(value) => setDraft((current) => ({ ...current, clear_api_key: value }))}
            />
          ) : null}
        </div>
        <div className="hint-box model-route-hint">
          <KeyRound size={14} />
          <span>
            {editingRoute
              ? `当前状态：${keyStatusLabel(editingRoute)}。留空表示不改，勾选“清空自定义 Key”后会回退到系统默认 Key。`
              : "未填写 API Key 时，会尝试使用系统默认 Key；正式推荐按优先级依次切换。"}
          </span>
        </div>
        <div className="inline-actions">
          <button className="ghost-btn primary" onClick={() => void saveDraft()} disabled={saving || !draft.base_url.trim() || !draft.model.trim()}>
            <Save size={14} />
            {editingRoute ? "保存路由" : "创建路由"}
          </button>
          <button className="ghost-btn" onClick={startCreate}>
            <Plus size={14} />
            新草稿
          </button>
        </div>
      </div>
    </section>
  );
});

function PanelTitle({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
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

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Tag({ text }: { text: string }) {
  const tone = text === "enabled" ? "positive" : "warning";
  return <span className="tag" data-tone={tone}>{text === "enabled" ? "启用" : "停用"}</span>;
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function PasswordField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="password" value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
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
    <label className="toggle-field">
      <span>{label}</span>
      <button
        className={`toggle ${value ? "on" : ""}`}
        type="button"
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
      >
        <span />
      </button>
    </label>
  );
}

function keyStatusLabel(route: ModelRoute) {
  if (route.key_source === "custom") return "自定义 Key";
  if (route.uses_default_key && route.default_key_available) return "系统默认 Key";
  return "未配置 Key";
}
