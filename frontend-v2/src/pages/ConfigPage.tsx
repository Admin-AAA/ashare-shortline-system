import { useEffect, useState } from "react";
import { Cpu, Clock, Database, KeyRound, Layers, Loader2, Plug, Save, Zap } from "lucide-react";
import { api } from "../api/client";
import type { SystemLlmConfig, SystemLlmTestResult } from "../types";
import { ErrorPage } from "../components/StateViews";
import { ModelRoutesSection } from "../components/ModelRoutesSection";
import { PresetsSection } from "../components/PresetsSection";
import { SchedulerTasksSection } from "../components/SchedulerTasksSection";
import { CollapsiblePanel } from "../components/CollapsiblePanel";
import { DataHealthSection } from "./DataHealthPage";

type ConfigTab = "scheduler" | "llm" | "presets" | "data-health";

/**
 * 配置页 —— 系统 LLM 接口（agent_skill_*）配置与连通性测试。
 *
 * - 读取当前配置（GET /api/admin/system-llm，API Key 脱敏为 ********）
 * - 表单修改 base_url / api_key / model / timeout / temperature / max_tokens
 * - 「测试连接」：用当前表单值真实发起一次 OpenAI 兼容调用并回显延迟/样本
 * - 「保存配置」：热更新内存 + 持久化到 .env（API Key 留空/******** 表示保留）
 */
type FormState = {
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds: string;
  temperature: string;
  max_tokens: string;
};

const EMPTY_FORM: FormState = {
  base_url: "",
  api_key: "",
  model: "",
  timeout_seconds: "",
  temperature: "",
  max_tokens: "",
};

const MASK = "********";

export function ConfigPage() {
  const [tab, setTab] = useState<ConfigTab>(() => {
    const q = new URLSearchParams(window.location.search).get("tab");
    return q === "data-health" || q === "llm" || q === "presets" ? q : "scheduler";
  });
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<SystemLlmTestResult | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .systemLlmConfig()
      .then((cfg: SystemLlmConfig) => {
        if (!alive) return;
        setForm({
          base_url: cfg.base_url,
          api_key: cfg.has_api_key ? MASK : "",
          model: cfg.model,
          timeout_seconds: String(cfg.timeout_seconds),
          temperature: String(cfg.temperature),
          max_tokens: String(cfg.max_tokens),
        });
        setLoaded(true);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  function update<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
    setSaveMsg(null);
    setTestResult(null);
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    setSaveMsg(null);
    try {
      const res = await api.testSystemLlmConfig({
        base_url: form.base_url || undefined,
        model: form.model || undefined,
        api_key: form.api_key === MASK ? undefined : form.api_key || undefined,
        timeout_seconds: form.timeout_seconds ? Number(form.timeout_seconds) : undefined,
        temperature: form.temperature ? Number(form.temperature) : undefined,
        max_tokens: form.max_tokens ? Number(form.max_tokens) : undefined,
      });
      setTestResult(res);
    } catch (e: unknown) {
      setTestResult({
        ok: false,
        latency_ms: 0,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setSaveMsg(null);
    try {
      const res = await api.updateSystemLlmConfig({
        base_url: form.base_url || undefined,
        model: form.model || undefined,
        api_key: form.api_key === MASK ? undefined : form.api_key || undefined,
        timeout_seconds: form.timeout_seconds ? Number(form.timeout_seconds) : undefined,
        temperature: form.temperature ? Number(form.temperature) : undefined,
        max_tokens: form.max_tokens ? Number(form.max_tokens) : undefined,
      });
      // 保存成功后用后端回显覆盖（api_key 重新脱敏）
      const cfg = res.config;
      setForm({
        base_url: cfg.base_url,
        api_key: cfg.has_api_key ? MASK : "",
        model: cfg.model,
        timeout_seconds: String(cfg.timeout_seconds),
        temperature: String(cfg.temperature),
        max_tokens: String(cfg.max_tokens),
      });
      setDirty(false);
      setSaveMsg(`已保存 ${res.updated.length} 项配置，即时生效。`);
    } catch (e: unknown) {
      setSaveMsg(`保存失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <ErrorPage title="配置加载失败" message={loadError} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {/* 二级 Tab 切换 */}
      <div className="sub-tabs" style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--border)" }}>
        <SubTab active={tab === "scheduler"} onClick={() => setTab("scheduler")}>
          <Clock size={14} />
          调度任务
        </SubTab>
        <SubTab active={tab === "llm"} onClick={() => setTab("llm")}>
          <Plug size={14} />
          LLM 接口
        </SubTab>
        <SubTab active={tab === "presets"} onClick={() => setTab("presets")}>
          <Layers size={14} />
          预设管理
        </SubTab>
        <SubTab active={tab === "data-health"} onClick={() => setTab("data-health")}>
          <Database size={14} />
          数据健康
        </SubTab>
      </div>

      {tab === "scheduler" && <SchedulerTasksSection />}

      {tab === "llm" && (
        <CollapsiblePanel
          collapsedKey="config-llm"
          kicker="配置"
          title="系统 LLM 接口"
          actions={
            <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "var(--text-sm)" }}>
              Skill Agent 推理通道（OpenAI 兼容）
            </span>
          }
        >
          <div
            style={{
              marginTop: "var(--space-4)",
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "var(--space-4)",
            }}
          >
        <Field label="Base URL" hint="OpenAI 兼容接口地址" icon={<Plug size={15} />}>
          <input
            className="cfg-input"
            value={form.base_url}
            placeholder="https://api.deepseek.com"
            onChange={(e) => update("base_url", e.target.value)}
          />
        </Field>

        <Field label="API Key" hint="留空表示保留当前 Key" icon={<KeyRound size={15} />}>
          <input
            className="cfg-input"
            type="password"
            value={form.api_key}
            placeholder={loaded && form.api_key === MASK ? "已配置（隐藏）" : "sk-..."}
            onChange={(e) => update("api_key", e.target.value)}
          />
        </Field>

        <Field label="模型" hint="如 deepseek-chat" icon={<Cpu size={15} />}>
          <input
            className="cfg-input"
            value={form.model}
            placeholder="deepseek-chat"
            onChange={(e) => update("model", e.target.value)}
          />
        </Field>

        <Field label="超时（秒）" hint="单次调用超时" icon={<Zap size={15} />}>
          <input
            className="cfg-input"
            type="number"
            min={1}
            step={1}
            value={form.timeout_seconds}
            onChange={(e) => update("timeout_seconds", e.target.value)}
          />
        </Field>

        <Field label="Temperature" hint="0 - 1，越低越稳" icon={<Zap size={15} />}>
          <input
            className="cfg-input"
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={form.temperature}
            onChange={(e) => update("temperature", e.target.value)}
          />
        </Field>

        <Field label="Max Tokens" hint="单次最大输出" icon={<Zap size={15} />}>
          <input
            className="cfg-input"
            type="number"
            min={1}
            step={1}
            value={form.max_tokens}
            onChange={(e) => update("max_tokens", e.target.value)}
          />
        </Field>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)", marginTop: "var(--space-5)", alignItems: "center" }}>
        <button className="cfg-btn cfg-btn--primary" onClick={handleTest} disabled={testing || !loaded}>
          {testing ? <Loader2 size={15} className="spin" /> : <Plug size={15} />}
          {testing ? "测试中…" : "测试连接"}
        </button>
        <button className="cfg-btn" onClick={handleSave} disabled={saving || !dirty}>
          {saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
          {saving ? "保存中…" : "保存配置"}
        </button>
        {dirty && !saving && (
          <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>有未保存的修改</span>
        )}
      </div>

      {saveMsg && (
        <div
          style={{
            marginTop: "var(--space-3)",
            padding: "var(--space-3)",
            borderRadius: 8,
            fontSize: "var(--text-sm)",
            background: saveMsg.startsWith("保存失败") ? "color-mix(in srgb, var(--color-down) 14%, transparent)" : "color-mix(in srgb, var(--color-up) 14%, transparent)",
            color: saveMsg.startsWith("保存失败") ? "var(--color-down)" : "var(--color-up)",
          }}
        >
          {saveMsg}
        </div>
      )}

      {testResult && (
        <div
          style={{
            marginTop: "var(--space-4)",
            padding: "var(--space-4)",
            borderRadius: 10,
            border: `1px solid ${testResult.ok ? "color-mix(in srgb, var(--color-up) 35%, transparent)" : "color-mix(in srgb, var(--color-down) 35%, transparent)"}`,
            background: testResult.ok ? "color-mix(in srgb, var(--color-up) 8%, transparent)" : "color-mix(in srgb, var(--color-down) 8%, transparent)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-2)" }}>
            <span
              style={{
                fontSize: "var(--text-xs)",
                fontWeight: 600,
                padding: "2px 10px",
                borderRadius: 999,
                color: testResult.ok ? "var(--color-up)" : "var(--color-down)",
                background: testResult.ok ? "color-mix(in srgb, var(--color-up) 16%, transparent)" : "color-mix(in srgb, var(--color-down) 16%, transparent)",
              }}
            >
              {testResult.ok ? "连通正常" : "连接失败"}
            </span>
            <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)" }}>
              延迟 {testResult.latency_ms} ms
              {testResult.model ? ` · ${testResult.model}` : ""}
            </span>
          </div>
          {testResult.ok ? (
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>
              模型回显：<span style={{ color: "var(--text)" }}>{testResult.sample || "（空响应）"}</span>
            </div>
          ) : (
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)", color: "var(--color-down)", whiteSpace: "pre-wrap" }}>
              {testResult.error}
            </div>
          )}
        </div>
      )}

      <p style={{ marginTop: "var(--space-5)", color: "var(--text-muted)", fontSize: "var(--text-xs)", lineHeight: 1.6 }}>
        测试与保存均作用于「系统 LLM 接口」（agent_skill_*）：Skill Agent 推理所调用的 OpenAI 兼容端点。
        保存会即时热更新运行中的配置并写入 .env 持久化，无需重启后端。
      </p>

          {/* 模型路由：调整优先级 + 逐条测试 + 配置独立 Key */}
          <ModelRoutesSection />
        </CollapsiblePanel>
      )}

      {tab === "presets" && <PresetsSection />}

      {tab === "data-health" && <DataHealthSection />}
    </div>
  );
}

function SubTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "10px 18px",
        background: "transparent",
        border: "none",
        borderBottom: active ? "2px solid var(--accent, var(--color-up))" : "2px solid transparent",
        color: active ? "var(--text)" : "var(--muted)",
        fontWeight: active ? 650 : 500,
        fontSize: "var(--text-sm)",
        cursor: "pointer",
        transition: "color 160ms ease, border-color 160ms ease",
        marginBottom: -1,
      }}
    >
      {children}
    </button>
  );
}

function Field({
  label,
  hint,
  icon,
  children,
}: {
  label: string;
  hint?: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "block" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6, color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>
        {icon}
        <span style={{ fontWeight: 600 }}>{label}</span>
        {hint && <span style={{ color: "var(--muted)", fontWeight: 400 }}>· {hint}</span>}
      </div>
      {children}
    </label>
  );
}
