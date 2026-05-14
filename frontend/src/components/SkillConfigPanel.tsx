import {
  BrainCircuit,
  Copy,
  Layers3,
  Plus,
  Save,
  SquarePen,
  Trash2,
} from "lucide-react";
import type { Preset, PresetDraft, PresetSkillDraft, RunMode, RunTarget, SkillSummary } from "../types";

type Props = {
  runTarget: RunTarget;
  setRunTarget: (target: RunTarget) => void;
  selectedPresetId: string;
  setSelectedPresetId: (id: string) => void;
  presets: Preset[];
  skills: SkillSummary[];
  editingPresetId: string | null;
  presetDraft: PresetDraft;
  onPresetDraftChange: (patch: Partial<PresetDraft>) => void;
  presetSkillDrafts: PresetSkillDraft[];
  onTogglePresetSkill: (skillId: string) => void;
  onUpdatePresetSkill: (skillId: string, patch: Partial<PresetSkillDraft>) => void;
  bucketLimits: Record<string, number>;
  onBucketLimitChange: (bucket: string, value: number) => void;
  singleSkillId: string;
  setSingleSkillId: (id: string) => void;
  singleSkillWeight: number;
  setSingleSkillWeight: (weight: number) => void;
  onSavePreset: () => void;
  onResetToNewPreset: () => void;
  onDuplicatePreset: () => void;
  onCopyPreset: () => void;
  onDeletePreset: () => void;
};

export default function SkillConfigPanel(props: Props) {
  const {
    runTarget,
    setRunTarget,
    selectedPresetId,
    setSelectedPresetId,
    presets,
    skills,
    editingPresetId,
    presetDraft,
    onPresetDraftChange,
    presetSkillDrafts,
    onTogglePresetSkill,
    onUpdatePresetSkill,
    bucketLimits,
    onBucketLimitChange,
    singleSkillId,
    setSingleSkillId,
    singleSkillWeight,
    setSingleSkillWeight,
    onSavePreset,
    onResetToNewPreset,
    onDuplicatePreset,
    onCopyPreset,
    onDeletePreset,
  } = props;

  return (
    <div className="skill-config-panel">
      <div className="preset-selector">
        <select
          value={selectedPresetId}
          onChange={(event) => setSelectedPresetId(event.target.value)}
          className="preset-select"
        >
          <optgroup label="正式">
            {presets.filter((p) => p.run_mode === "formal").map((p) => (
              <option key={p.preset_id} value={p.preset_id}>{p.name}</option>
            ))}
          </optgroup>
          <optgroup label="实验">
            {presets.filter((p) => p.run_mode === "experiment").map((p) => (
              <option key={p.preset_id} value={p.preset_id}>{p.name}</option>
            ))}
          </optgroup>
        </select>
        <button className="ghost-btn" onClick={onResetToNewPreset}>
          <Plus size={14} />
          新建
        </button>
        <button className="ghost-btn" onClick={onCopyPreset}>
          <Copy size={14} />
          复制
        </button>
        <button
          className="ghost-btn danger"
          onClick={onDeletePreset}
          disabled={!selectedPresetId}
          title="删除当前预设"
        >
          <Trash2 size={14} />
          删除
        </button>
      </div>

      <div className="mode-switch">
        <button className={runTarget === "preset" ? "active" : ""} onClick={() => setRunTarget("preset")}>
          <Layers3 size={14} />
          运行预设
        </button>
        <button className={runTarget === "single-skill" ? "active" : ""} onClick={() => setRunTarget("single-skill")}>
          <BrainCircuit size={14} />
          单 Skill
        </button>
      </div>

      {runTarget === "preset" ? (
        <>
          <div className="config-card">
            <h3 className="config-card-title">基本参数</h3>
            <div className="form-grid">
              <Field label="名称" value={presetDraft.name} onChange={(value) => onPresetDraftChange({ name: value })} />
              <Field
                label="描述"
                value={presetDraft.description}
                onChange={(value) => onPresetDraftChange({ description: value })}
                multiline
              />
              <SelectField
                label="模式"
                value={presetDraft.run_mode}
                options={[
                  { value: "formal", label: "正式" },
                  { value: "experiment", label: "实验" },
                ]}
                onChange={(value) => onPresetDraftChange({ run_mode: value as RunMode })}
              />
              <SelectField
                label="市场范围"
                value={presetDraft.market_scope}
                options={[
                  { value: "all", label: "全部" },
                  { value: "watchlist", label: "自选股" },
                  { value: "industry", label: "行业" },
                ]}
                onChange={(value) => onPresetDraftChange({ market_scope: value })}
              />
              <Field
                label="范围值"
                value={presetDraft.market_scope_value}
                onChange={(value) => onPresetDraftChange({ market_scope_value: value })}
              />
              <SelectField
                label="风控模板"
                value={presetDraft.risk_template}
                options={[
                  { value: "conservative", label: "保守" },
                  { value: "balanced", label: "均衡" },
                  { value: "aggressive", label: "激进" },
                ]}
                onChange={(value) => onPresetDraftChange({ risk_template: value })}
              />
              <NumberField
                label="输出数量"
                value={presetDraft.output_count}
                min={1}
                max={30}
                onChange={(value) => onPresetDraftChange({ output_count: value })}
              />
              <ToggleField
                label="隐藏近期重复票"
                value={presetDraft.hide_recent_repeats}
                onChange={(value) => onPresetDraftChange({ hide_recent_repeats: value })}
              />
              <SelectField
                label="运行数据"
                value={presetDraft.data_source}
                options={[
                  { value: "local_pool", label: "仅本地池" },
                  { value: "local_pool_preferred", label: "本地池优先" },
                  { value: "realtime", label: "仅实时数据源" },
                ]}
                onChange={(value) => onPresetDraftChange({ data_source: value })}
              />
            </div>
          </div>

          <div className="config-card">
            <div className="config-card-head">
              <h3 className="config-card-title">Skill 组合</h3>
              <span className="config-card-count">{presetSkillDrafts.length} 个已启用</span>
            </div>
            <div className="skill-picker">
              {skills.map((skill) => {
                const enabled = presetSkillDrafts.some((item) => item.skill_id === skill.skill_id);
                const draftItem = presetSkillDrafts.find((item) => item.skill_id === skill.skill_id);
                return (
                  <div key={skill.skill_id} className={`skill-picker-row ${enabled ? "enabled" : ""}`}>
                    <button
                      className={`skill-toggle ${enabled ? "on" : ""}`}
                      type="button"
                      role="switch"
                      aria-checked={enabled}
                      aria-label={`${enabled ? "移除" : "加入"} ${skill.name}`}
                      onClick={() => onTogglePresetSkill(skill.skill_id)}
                    >
                      {enabled ? "已选" : "加入"}
                    </button>
                    <div className="skill-picker-meta">
                      <strong>{skill.name}</strong>
                      <span>{displayText(skill.mode)}</span>
                    </div>
                    {enabled ? (
                      <input
                        className="weight-input"
                        type="number"
                        min="0.1"
                        max="5"
                        step="0.05"
                        value={draftItem?.weight ?? 1}
                        onChange={(event) =>
                          onUpdatePresetSkill(skill.skill_id, { weight: Number(event.target.value) || 1 })
                        }
                      />
                    ) : (
                      <span className="weight-placeholder">-</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="config-card">
            <h3 className="config-card-title">分桶限制</h3>
            <div className="bucket-limits-row">
              <NumberField
                label="容量核心"
                value={bucketLimits["容量核心"] ?? 3}
                min={0}
                max={20}
                onChange={(value) => onBucketLimitChange("容量核心", value)}
              />
              <NumberField
                label="高弹观察"
                value={bucketLimits["高弹观察"] ?? 2}
                min={0}
                max={20}
                onChange={(value) => onBucketLimitChange("高弹观察", value)}
              />
            </div>
          </div>

          <div className="inline-actions">
            <button className="ghost-btn" type="button" onClick={onSavePreset}>
              <Save size={14} />
              保存预设
            </button>
            <button className="ghost-btn" type="button" onClick={onResetToNewPreset}>
              <SquarePen size={14} />
              新草稿
            </button>
          </div>
        </>
      ) : (
        <div className="config-card">
          <h3 className="config-card-title">单 Skill 实验</h3>
          <div className="form-grid">
            <SelectField
              label="Skill"
              value={singleSkillId}
              options={skills.map((skill) => ({ value: skill.skill_id, label: skill.name }))}
              onChange={setSingleSkillId}
            />
            <NumberField label="权重" value={singleSkillWeight} min={0.1} max={5} step={0.05} onChange={setSingleSkillWeight} />
          </div>
          <div className="hint-box">单 Skill 运行默认进入实验，不污染正式统计。</div>
        </div>
      )}
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

function displayText(key: string): string {
  const labels: Record<string, string> = {
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
  };
  return labels[key] ?? key;
}
