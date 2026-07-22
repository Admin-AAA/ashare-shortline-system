import { useEffect, useState } from "react";
import {
  Loader2,
  Pencil,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../api/client";
import type { Preset, SkillSummary } from "../types";
import { RISK_TEMPLATE_MAP, DATA_SOURCE_MAP } from "../utils/riskTemplates";

/**
 * 预设管理 —— 配置页「预设管理」Tab。
 *
 * 填补缺口：此前系统没有创建/编辑预设的前端入口，用户只能通过 API
 * 或数据库操作预设，导致新增策略后无法挂到预设、运行页看不到新策略。
 *
 * 功能：
 * - 列出全部预设（名称 / 模式 / 分数线 / 包含策略）
 * - 新建预设（含策略多选 + 权重）
 * - 编辑预设（展开行内表单）
 * - 删除预设
 */
// 保留原始值用于内部逻辑处理
const RISK_TEMPLATES = ["balanced", "aggressive", "conservative"];
const DATA_SOURCES = ["local_pool_preferred", "auto"];

type SkillPick = {
  skill_id: string;
  name: string;
  selected: boolean;
  weight: number;
};

type PresetForm = {
  name: string;
  description: string;
  skip_aux_refresh: boolean;
  market_scope: string;
  market_scope_value: string;
  risk_template: string;
  score_line: number;
  output_count: number;
  data_source: string;
  hide_recent_repeats: boolean;
  skills: SkillPick[];
};

function emptyForm(skills: SkillSummary[]): PresetForm {
  return {
    name: "",
    description: "",
    skip_aux_refresh: false,
    market_scope: "all",
    market_scope_value: "",
    risk_template: "balanced",
    score_line: 72,
    output_count: 6,
    data_source: "local_pool_preferred",
    hide_recent_repeats: true,
    skills: skills.map((s) => ({
      skill_id: s.skill_id,
      name: s.name,
      selected: false,
      weight: 1,
    })),
  };
}

function formFromPreset(p: Preset, skills: SkillSummary[]): PresetForm {
  const picked = new Map<string, { weight: number; sort_order: number }>();
  for (const s of p.skills) {
    picked.set(s.skill_id, { weight: s.weight, sort_order: s.sort_order });
  }
  return {
    name: p.name,
    description: p.description ?? "",
    skip_aux_refresh: p.skip_aux_refresh,
    market_scope: p.market_scope,
    market_scope_value: p.market_scope_value ?? "",
    risk_template: p.risk_template,
    score_line: p.score_line,
    output_count: p.output_count,
    data_source: p.data_source,
    hide_recent_repeats: p.hide_recent_repeats,
    skills: skills
      .map((s) => {
        const hit = picked.get(s.skill_id);
        return {
          skill_id: s.skill_id,
          name: s.name,
          selected: !!hit,
          weight: hit?.weight ?? 1,
        };
      })
      // 已选策略排前面
      .sort((a, b) => Number(b.selected) - Number(a.selected)),
  };
}

function formToPayload(f: PresetForm) {
  return {
    name: f.name,
    description: f.description || null,
    skip_aux_refresh: f.skip_aux_refresh,
    market_scope: f.market_scope,
    market_scope_value: f.market_scope_value || null,
    risk_template: f.risk_template,
    score_line: f.score_line,
    output_count: f.output_count,
    data_source: f.data_source,
    hide_recent_repeats: f.hide_recent_repeats,
    skills: f.skills
      .filter((s) => s.selected)
      .map((s, i) => ({
        skill_id: s.skill_id,
        weight: s.weight,
        sort_order: i,
      })),
  };
}

export function PresetsSection() {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [adding, setAdding] = useState(false);
  const [createForm, setCreateForm] = useState<PresetForm | null>(null);
  const [saving, setSaving] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<PresetForm | null>(null);
  const [editSaving, setEditSaving] = useState(false);

  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const [ps, sks] = await Promise.all([api.presets(), api.skills()]);
      setPresets(ps);
      setSkills(sks);
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

  function startCreate() {
    setCreateForm(emptyForm(skills));
    setAdding(true);
  }

  async function handleCreate() {
    if (!createForm) return;
    if (!createForm.name.trim()) {
      showToast("预设名称不能为空");
      return;
    }
    if (!createForm.skills.some((s) => s.selected)) {
      showToast("至少选择一个策略");
      return;
    }
    setSaving(true);
    try {
      await api.createPreset(formToPayload(createForm));
      showToast("预设已创建");
      setAdding(false);
      setCreateForm(null);
      await reload();
    } catch (e) {
      showToast(`创建失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  }

  function startEdit(p: Preset) {
    setEditForm(formFromPreset(p, skills));
    setEditingId(p.preset_id);
  }

  async function handleUpdate(presetId: string) {
    if (!editForm) return;
    if (!editForm.name.trim()) {
      showToast("预设名称不能为空");
      return;
    }
    if (!editForm.skills.some((s) => s.selected)) {
      showToast("至少选择一个策略");
      return;
    }
    setEditSaving(true);
    try {
      await api.updatePreset(presetId, formToPayload(editForm));
      showToast("预设已保存");
      setEditingId(null);
      setEditForm(null);
      await reload();
    } catch (e) {
      showToast(`保存失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setEditSaving(false);
    }
  }

  async function handleDelete(presetId: string, name: string) {
    if (!confirm(`确认删除预设「${name}」？此操作不可撤销。`)) return;
    setBusyId(presetId);
    try {
      await api.deletePreset(presetId);
      showToast("预设已删除");
      if (editingId === presetId) {
        setEditingId(null);
        setEditForm(null);
      }
      await reload();
    } catch (e) {
      showToast(`删除失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  }

  if (loading) {
    return (
      <div style={{ padding: "var(--space-6)", textAlign: "center", color: "var(--muted)" }}>
        <Loader2 size={18} className="spin" style={{ marginRight: 8 }} />
        正在加载预设…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: "var(--space-4)", color: "var(--color-down)", fontSize: "var(--text-sm)" }}>
        预设加载失败：{error}
        <button className="cfg-btn" style={{ marginLeft: 12 }} onClick={reload}>
          重试
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {/* 顶部操作条 */}
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
          共 {presets.length} 个预设 · {skills.length} 个策略可选
        </span>
        <button className="cfg-btn cfg-btn--primary" onClick={startCreate} disabled={adding}>
          <Plus size={15} />
          新建预设
        </button>
      </div>

      {/* 新建表单 */}
      {adding && createForm && (
        <PresetEditor
          title="新建预设"
          form={createForm}
          onChange={setCreateForm}
          onSave={handleCreate}
          onCancel={() => {
            setAdding(false);
            setCreateForm(null);
          }}
          saving={saving}
        />
      )}

      {/* 预设列表 */}
      {presets.length === 0 && !adding ? (
        <div style={{ padding: "var(--space-6)", textAlign: "center", color: "var(--muted)" }}>
          暂无预设，点击「新建预设」创建第一个
        </div>
      ) : (
        presets.map((p) => (
          <div key={p.preset_id} style={cardStyle}>
            {/* 预设摘要 */}
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--space-3)" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ fontWeight: 650, fontSize: "var(--text-lg)", color: "var(--text)" }}>{p.name}</span>
                  <span style={pillStyle(p.status === "active" ? "var(--color-up)" : "var(--muted)")}>
                    {p.status === "active" ? "启用" : "停用"}
                  </span>
                </div>
                <div style={{ color: "var(--muted)", fontSize: "var(--text-xs)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                  {p.preset_id}
                </div>
              </div>
              <div style={{ display: "flex", gap: "var(--space-2)", flexShrink: 0 }}>
                <button
                  className="icon-button"
                  title="编辑"
                  onClick={() => (editingId === p.preset_id ? cancelEdit() : startEdit(p))}
                >
                  <Pencil size={15} />
                </button>
                <button
                  className="icon-button"
                  title="删除"
                  onClick={() => handleDelete(p.preset_id, p.name)}
                  disabled={busyId === p.preset_id}
                >
                  {busyId === p.preset_id ? <Loader2 size={15} className="spin" /> : <Trash2 size={15} />}
                </button>
              </div>
            </div>

            {/* 元信息 */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "var(--space-3)", marginTop: "var(--space-3)" }}>
              <Metric label="分数线" value={String(p.score_line)} />
              <Metric label="输出数" value={String(p.output_count)} />
              <Metric label="风险模板" value={RISK_TEMPLATE_MAP[p.risk_template] || p.risk_template} />
              <Metric label="数据源" value={DATA_SOURCE_MAP[p.data_source] || p.data_source} />
              <Metric label="隐藏重复" value={p.hide_recent_repeats ? "是" : "否"} />
            </div>

            {/* 包含策略 */}
            <div style={{ marginTop: "var(--space-3)" }}>
              <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)", letterSpacing: "0.04em", marginBottom: 4 }}>
                包含策略（{p.skills.length}）
              </div>
              {p.skills.length === 0 ? (
                <span style={{ color: "var(--color-down)", fontSize: "var(--text-sm)" }}>
                  ⚠ 未分配任何策略，运行时将报错
                </span>
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
                  {p.skills.map((s) => (
                    <span key={s.skill_id} style={skillTagStyle}>
                      {s.name}
                      <span style={{ color: "var(--muted)", marginLeft: 4, fontFamily: "var(--font-mono)" }}>
                        ×{s.weight}
                      </span>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* 编辑表单 */}
            {editingId === p.preset_id && editForm && (
              <PresetEditor
                title="编辑预设"
                form={editForm}
                onChange={setEditForm}
                onSave={() => handleUpdate(p.preset_id)}
                onCancel={cancelEdit}
                saving={editSaving}
              />
            )}
          </div>
        ))
      )}

      {/* toast */}
      {toast && (
        <div
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            padding: "10px 18px",
            borderRadius: 8,
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.16)",
            fontSize: "var(--text-sm)",
            color: "var(--text)",
            zIndex: 1000,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );

  function cancelEdit() {
    setEditingId(null);
    setEditForm(null);
  }
}

// ─── 预设编辑器（新建 / 编辑共用） ───────────────────────────
function PresetEditor({
  title,
  form,
  onChange,
  onSave,
  onCancel,
  saving,
}: {
  title: string;
  form: PresetForm;
  onChange: (f: PresetForm) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const selectedCount = form.skills.filter((s) => s.selected).length;

  function set<K extends keyof PresetForm>(key: K, value: PresetForm[K]) {
    onChange({ ...form, [key]: value });
  }

  function toggleSkill(skillId: string) {
    onChange({
      ...form,
      skills: form.skills.map((s) =>
        s.skill_id === skillId ? { ...s, selected: !s.selected } : s,
      ),
    });
  }

  function setWeight(skillId: string, weight: number) {
    onChange({
      ...form,
      skills: form.skills.map((s) =>
        s.skill_id === skillId ? { ...s, weight } : s,
      ),
    });
  }

  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "var(--space-4)",
        borderRadius: 10,
        border: "1px solid var(--border)",
        background: "var(--surface)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-3)" }}>
        <span style={{ fontWeight: 650, fontSize: "var(--text-base)", color: "var(--text)" }}>{title}</span>
        <button className="icon-button" onClick={onCancel} title="取消">
          <X size={16} />
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "var(--space-3)" }}>
        <FormField label="名称">
          <input className="cfg-input" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="如：全市场" />
        </FormField>
        <FormField label="分数线">
          <input className="cfg-input" type="number" min={0} max={100} step={0.5} value={form.score_line} onChange={(e) => set("score_line", Number(e.target.value))} />
        </FormField>
        <FormField label="输出数量">
          <input className="cfg-input" type="number" min={1} max={50} step={1} value={form.output_count} onChange={(e) => set("output_count", Number(e.target.value))} />
        </FormField>
        <FormField label="风险模板">
          <select className="cfg-input" value={form.risk_template} onChange={(e) => set("risk_template", e.target.value)}>
            {RISK_TEMPLATES.map((t) => (
              <option key={t} value={t}>{RISK_TEMPLATE_MAP[t] || t}</option>
            ))}
          </select>
        </FormField>
        <FormField label="数据源">
          <select className="cfg-input" value={form.data_source} onChange={(e) => set("data_source", e.target.value)}>
            {DATA_SOURCES.map((d) => (
              <option key={d} value={d}>{DATA_SOURCE_MAP[d] || d}</option>
            ))}
          </select>
        </FormField>
        <FormField label="市场范围">
          <select className="cfg-input" value={form.market_scope} onChange={(e) => set("market_scope", e.target.value)}>
            <option value="all">全市场</option>
            <option value="industry">按行业</option>
            <option value="watchlist">观察池</option>
          </select>
        </FormField>
        <FormField label="范围值（行业/池名）">
          <input className="cfg-input" value={form.market_scope_value} onChange={(e) => set("market_scope_value", e.target.value)} placeholder="选行业/池时填写" disabled={form.market_scope === "all"} />
        </FormField>
      </div>

      <div style={{ marginTop: "var(--space-3)" }}>
        <FormField label="描述">
          <input className="cfg-input" value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="可选" />
        </FormField>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", marginTop: "var(--space-3)" }}>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", cursor: "pointer" }}>
          <input type="checkbox" checked={form.hide_recent_repeats} onChange={(e) => set("hide_recent_repeats", e.target.checked)} />
          <span style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>隐藏近期重复推荐</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", cursor: "pointer" }}>
          <input type="checkbox" checked={form.skip_aux_refresh} onChange={(e) => set("skip_aux_refresh", e.target.checked)} />
          <span style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>跳过辅助数据刷新（适用矩阵快测）</span>
        </label>
      </div>

      {/* 策略多选 */}
      <div style={{ marginTop: "var(--space-4)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-2)" }}>
          <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text-secondary)" }}>
            包含策略（已选 {selectedCount} / {form.skills.length}）
          </span>
        </div>
        <div style={{ display: "grid", gap: "var(--space-2)" }}>
          {form.skills.map((s) => {
            return (
              <div
                key={s.skill_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-3)",
                  padding: "8px 12px",
                  borderRadius: 8,
                  background: s.selected ? "color-mix(in srgb, var(--color-up) 8%, transparent)" : "var(--surface-1)",
                  border: `1px solid ${s.selected ? "color-mix(in srgb, var(--color-up) 28%, transparent)" : "var(--border)"}`,
                }}
              >
                <input
                  type="checkbox"
                  checked={s.selected}
                  onChange={() => toggleSkill(s.skill_id)}
                />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <span style={{ fontWeight: 600, fontSize: "var(--text-sm)", color: "var(--text)" }}>{s.name}</span>
                  <span style={{ marginLeft: 8, fontSize: "var(--text-xs)", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>{s.skill_id}</span>
                </div>
                {s.selected && (
                  <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "var(--text-sm)", color: "var(--muted)", flexShrink: 0 }}>
                    权重
                    <input
                      type="number"
                      min={0}
                      step={0.1}
                      value={s.weight}
                      onChange={(e) => setWeight(s.skill_id, Number(e.target.value))}
                      style={{ width: 56, padding: "4px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--surface-1)", color: "var(--text)", fontFamily: "var(--font-mono)" }}
                    />
                  </label>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 操作按钮 */}
      <div style={{ display: "flex", gap: "var(--space-3)", marginTop: "var(--space-4)", alignItems: "center" }}>
        <button className="cfg-btn cfg-btn--primary" onClick={onSave} disabled={saving}>
          {saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
          {saving ? "保存中…" : "保存预设"}
        </button>
        <button className="cfg-btn" onClick={onCancel} disabled={saving}>
          取消
        </button>
      </div>
    </div>
  );
}

// ─── 子组件 ──────────────────────────────────────────────────
function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "block" }}>
      <div style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", fontWeight: 600, marginBottom: 4 }}>{label}</div>
      {children}
    </label>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text)", fontFamily: "var(--font-mono)" }}>{value}</div>
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--surface-soft)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "var(--space-4)",
  display: "flex",
  flexDirection: "column",
};

function pillStyle(color: string): React.CSSProperties {
  return {
    fontSize: "var(--text-xs)",
    fontWeight: 600,
    color,
    background: `color-mix(in srgb, ${color} 14%, transparent)`,
    borderRadius: 999,
    padding: "2px 10px",
    whiteSpace: "nowrap",
  };
}

const skillTagStyle: React.CSSProperties = {
  fontSize: "var(--text-xs)",
  color: "var(--text-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 999,
  padding: "3px 10px",
  whiteSpace: "nowrap",
  background: "var(--surface-1)",
};
