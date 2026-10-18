import {
  ArrowLeft,
  CheckCircle2,
  Clock3,
  FileCode2,
  GitCompareArrows,
  History,
  ShieldAlert,
  Target,
} from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../app/useApi";
import { useUrlRoute } from "../app/useUrlRoute";
import { EmptyState, ErrorPage, PageSkeleton } from "../components/StateViews";
import type {
  SkillStatsRow,
  StrategyCenterDetail,
  StrategyVersionChange,
  StrategyVersionUsage,
} from "../types";
import { formatBj } from "../utils/time";

type DetailTab = "rules" | "changes" | "snapshot";

export function StrategyDetailPage({ refreshSignal = 0 }: { refreshSignal?: number }) {
  const { pathname, params, navigate } = useUrlRoute();
  const strategyId = decodeURIComponent(pathname.slice("/strategies/".length));
  const versionId = params.get("version_id") || undefined;
  const [tab, setTab] = useState<DetailTab>("rules");
  const { data, loading, error } = useApi<StrategyCenterDetail>(
    () => api.strategyDetail(strategyId, versionId),
    [strategyId, versionId, refreshSignal],
  );

  if (loading) return <PageSkeleton title="策略详情" hint="加载策略版本与规则" rowCount={8} />;
  if (error) return <ErrorPage title="策略详情加载失败" message={error} />;
  if (!data) return <EmptyState icon={<FileCode2 size={32} />} title="策略不存在" reason="找不到该策略" />;

  const selectedUsage = data.timeline.find((item) => item.version_id === data.selected_version_id);
  const versionLabel = data.snapshot?.declared_version || selectedUsage?.declared_version || "未标注";

  const selectVersion = (item: StrategyVersionUsage) => {
    navigate(`/strategies/${encodeURIComponent(strategyId)}`, {
      params: { version_id: item.is_current ? null : item.version_id },
    });
  };

  return (
    <div className="strategy-center">
      <div className="strategy-center__toolbar">
        <button className="strategy-center__back" onClick={() => navigate("/strategies")}>
          <ArrowLeft size={15} /> 返回策略清单
        </button>
        <span>策略内容直接来自运行契约，刷新后自动读取当前生效版本</span>
      </div>

      <div className="strategy-center__layout">
        <aside className="strategy-timeline" aria-label="策略版本时间线">
          <div className="strategy-timeline__heading">
            <div>
              <span className="strategy-center__eyebrow">版本档案</span>
              <h2>{data.timeline.length} 个版本</h2>
            </div>
            <History size={18} />
          </div>
          <div className="strategy-timeline__list">
            {data.timeline.map((item) => (
              <button
                key={item.version_id}
                className={`strategy-version${item.version_id === data.selected_version_id ? " strategy-version--selected" : ""}`}
                onClick={() => selectVersion(item)}
                aria-pressed={item.version_id === data.selected_version_id}
              >
                <span className="strategy-version__rail" />
                <span className="strategy-version__topline">
                  <strong>v{item.declared_version || "未标注"}</strong>
                  {item.is_current && <span className="strategy-version__current">当前</span>}
                </span>
                <span className="strategy-version__hash">{shortId(item.version_id, 18)}</span>
                <span className="strategy-version__meta">
                  {formatDateTime(item.created_at)} · {item.snapshot_available ? "快照完整" : "旧快照缺失"}
                </span>
                <span className="strategy-version__usage">
                  {item.run_count} 次运行 · {item.recommendation_count} 笔推荐
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="strategy-detail">
          <header className="strategy-detail__header">
            <div>
              <div className="strategy-detail__statusline">
                <span className={`strategy-status strategy-status--${data.enabled ? "active" : "disabled"}`}>
                  {data.enabled ? "正在生效" : "已停用"}
                </span>
                <span className="strategy-status">{data.mode === "declarative" ? "声明式" : "命令式"}</span>
                {!data.selected_is_current && <span className="strategy-status strategy-status--history">历史快照</span>}
              </div>
              <h1>{data.name}</h1>
              <p>{data.description || "暂无策略简介"}</p>
            </div>
            <div className="strategy-detail__identity">
              <span>运行版本</span>
              <strong>v{versionLabel}</strong>
              <code title={data.selected_version_id}>{shortId(data.selected_version_id, 24)}</code>
              <span>归档于 {formatDateTime(selectedUsage?.created_at || null)}</span>
            </div>
          </header>

          <div className="strategy-detail__metrics">
            <Metric label="原始信号" value={data.performance ? `${data.performance.raw_signal_count} 个` : "—"} />
            <Metric label="Top10 已结算" value={data.performance ? `${data.performance.sample_count} 个` : "—"} />
            <Metric
              label="Top10 胜率"
              value={data.performance?.hit_rate == null ? "—" : `${(data.performance.hit_rate * 100).toFixed(1)}%`}
              tone={data.performance?.hit_rate != null && data.performance.hit_rate >= 0.5 ? "up" : undefined}
            />
            <Metric
              label="平均单笔收益"
              value={data.performance?.avg_return == null ? "—" : `${data.performance.avg_return >= 0 ? "+" : ""}${data.performance.avg_return.toFixed(2)}%`}
              tone={data.performance?.avg_return == null ? undefined : data.performance.avg_return >= 0 ? "up" : "down"}
            />
          </div>

          {data.performance ? (
            <div style={{ marginTop: 12, padding: 12, border: "1px solid var(--border)", borderRadius: 10 }}>
              <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 8 }} title={data.performance.strategy_version}>
                当前版本 canonical 回测 · 每日评分 Top {data.performance.daily_selection_limit} · 不含仓位/资金模型 · {data.performance.date_from} ~ {data.performance.date_to} · 更新 {formatBj(data.performance.generated_at)}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(6, minmax(80px, 1fr))", gap: 8 }}>
                {data.performance.monthly_metrics.map((month) => (
                  <div key={month.month} style={{ background: "var(--surface-soft)", borderRadius: 8, padding: 8, textAlign: "center" }}>
                    <div style={{ color: "var(--muted)", fontSize: 11 }}>{month.month}</div>
                    <div style={{ fontFamily: "var(--font-mono)", fontWeight: 650, color: month.avg_return == null ? "var(--muted)" : month.avg_return >= 0 ? "var(--market-up)" : "var(--market-down)" }}>
                      胜率 {month.hit_rate == null ? "—" : `${(month.hit_rate * 100).toFixed(1)}%`}
                    </div>
                    <div style={{ color: "var(--muted)", fontSize: 10 }}>
                      均收益 {month.avg_return == null ? "—" : `${month.avg_return >= 0 ? "+" : ""}${month.avg_return.toFixed(2)}%`}
                    </div>
                    <div style={{ color: "var(--muted)", fontSize: 10 }}>Top10 {month.selected_signal_count} / 原始 {month.raw_signal_count}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ marginTop: 12, color: "var(--muted)", fontSize: 12 }}>
              {data.performance_status === "selected_version_not_current"
                ? "历史版本不复用当前画像；这里只展示当前可执行版本的半年回测。"
                : "当前版本半年回测画像待生成；未使用跨版本真实收益补位。"}
            </div>
          )}

          <StrategyFingerprint strategyId={data.strategy_id} />

          <div className="strategy-detail__actions">
            <div className="strategy-tabs" role="tablist" aria-label="策略详情视图">
              <TabButton active={tab === "rules"} onClick={() => setTab("rules")} icon={<Target size={15} />}>策略规则</TabButton>
              <TabButton active={tab === "changes"} onClick={() => setTab("changes")} icon={<GitCompareArrows size={15} />}>版本差异</TabButton>
              <TabButton active={tab === "snapshot"} onClick={() => setTab("snapshot")} icon={<FileCode2 size={15} />}>完整快照</TabButton>
            </div>
            <button
              className="strategy-history-link"
              onClick={() => navigate("/history", {
                params: {
                  strategy_id: data.strategy_id,
                  strategy_name: data.name,
                  strategy_version_id: data.selected_version_id,
                  strategy_version: versionLabel,
                },
              })}
            >
              <History size={15} /> 查看该版本推荐
            </button>
          </div>

          {!data.snapshot_available ? (
            <div className="strategy-snapshot-missing">
              <ShieldAlert size={22} />
              <div>
                <strong>这个旧版本没有保存正文快照</strong>
                <p>系统只保留了版本号和哈希，不能用当前文件冒充历史内容。从本次升级后的版本开始会完整归档。</p>
              </div>
            </div>
          ) : tab === "rules" ? (
            <StrategyRules data={data} />
          ) : tab === "changes" ? (
            <StrategyChanges data={data} />
          ) : (
            <StrategySnapshot data={data} />
          )}
        </section>
      </div>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  return (
    <div className="strategy-metric">
      <span>{label}</span>
      <strong className={tone ? `strategy-metric--${tone}` : ""}>{value}</strong>
    </div>
  );
}

function StrategyFingerprint({ strategyId }: { strategyId: string }) {
  const { data: rows, loading, error } = useApi<SkillStatsRow[]>(() => api.statsBySkill(), [strategyId]);
  const row = rows?.find((item) => item.skill_id === strategyId);
  if (loading) {
    return (
      <div className="strategy-fingerprint strategy-fingerprint--loading">
        <div className="strategy-fingerprint__skeleton" />
      </div>
    );
  }
  if (error || !row) return null;
  const blocks: Array<{ title: string; items?: Array<{ name: string; count: number }> }> = [
    { title: "市场阶段", items: row.top_market_regimes },
    { title: "行业分布", items: row.top_industries },
    { title: "入场形态", items: row.top_setups },
  ];
  if (blocks.every((block) => !block.items || block.items.length === 0)) {
    return (
      <div className="strategy-fingerprint">
        <p className="strategy-fingerprint__empty">该策略暂未积累足够的标注样本来生成画像。</p>
      </div>
    );
  }
  return (
    <section className="strategy-fingerprint" aria-label="策略画像">
      <header className="strategy-fingerprint__header">
        <h2>策略画像</h2>
        <p>基于 {row.sample_count} 笔已标注样本的真实分布 — 看清这个策略在哪些市场阶段、行业与形态上真正被触发。</p>
      </header>
      <div className="strategy-fingerprint__grid">
        {blocks.map((block) => (
          <FingerprintBlock key={block.title} title={block.title} items={block.items} />
        ))}
      </div>
    </section>
  );
}

function FingerprintBlock({ title, items }: { title: string; items?: Array<{ name: string; count: number }> }) {
  if (!items || items.length === 0) return null;
  const max = Math.max(...items.map((item) => item.count));
  return (
    <div className="fingerprint-block">
      <h3>{title}<span>{items.length}</span></h3>
      <ul className="fingerprint-bars">
        {items.map((item, index) => {
          const ratio = max > 0 ? (item.count / max) * 100 : 0;
          const isLead = index === 0;
          return (
            <li key={item.name} className={isLead ? "fingerprint-bar fingerprint-bar--lead" : "fingerprint-bar"}>
              <span className="fingerprint-bar__label" title={item.name}>{item.name}</span>
              <span className="fingerprint-bar__track" aria-hidden="true">
                <span className="fingerprint-bar__fill" style={{ width: `${ratio}%` }} />
              </span>
              <span className="fingerprint-bar__value">{item.count}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function TabButton({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button className={active ? "strategy-tab strategy-tab--active" : "strategy-tab"} onClick={onClick} role="tab" aria-selected={active}>
      {icon}{children}
    </button>
  );
}

function StrategyRules({ data }: { data: StrategyCenterDetail }) {
  const snapshot = data.snapshot!;
  const contract = asRecord(snapshot.contract);
  if (!contract) {
    return (
      <div className="strategy-rules">
        <RuleSection title="命令式策略说明" description="该策略由 Python 实现执行，没有 strategy.yaml 规则树。">
          <pre className="strategy-skill-body">{snapshot.skill_body || "暂无 SKILL.md 说明"}</pre>
        </RuleSection>
      </div>
    );
  }

  const universe = asRecord(contract.universe);
  const prefilter = asRecord(asRecord(contract.prefilter)?.market);
  const scoring = asRecord(contract.scoring);
  const risk = asRecord(contract.risk);
  const meta = asRecord(contract.meta);
  return (
    <div className="strategy-rules">
      <RuleSection title="策略身份" description="运行时实际采用的权重、周期和策略元数据。">
        <KeyValueGrid values={{ weight: contract.weight, ...meta }} />
      </RuleSection>
      <RuleSection title="股票池" description="先决定哪些股票有资格进入该策略。">
        <RuleColumns
          groups={[
            { title: "必须满足", items: asArray(universe?.include), tone: "include" },
            { title: "直接排除", items: asArray(universe?.exclude), tone: "exclude" },
          ]}
        />
      </RuleSection>
      <RuleSection title="前置筛选" description="进入完整评分前执行的市场条件。">
        <RuleColumns
          groups={[
            { title: "全部满足", items: asArray(prefilter?.include), tone: "include" },
            { title: "满足任一", items: asArray(prefilter?.include_any), tone: "any" },
            { title: "风险排除", items: asArray(prefilter?.exclude), tone: "exclude" },
          ]}
        />
      </RuleSection>
      <ObjectCards title="市场阶段" items={asArray(contract.phases)} />
      <ObjectCards title="入场形态" items={asArray(contract.setups)} />
      <RuleSection title="评分规则" description={`基础分 ${formatScalar(scoring?.base)}`}>
        <ObjectTable items={asArray(scoring?.rules)} />
      </RuleSection>
      {risk && (
        <RuleSection title="风险与否决" description="命中后可能降分、封顶或直接否决。">
          <ObjectTable items={asArray(risk.rules)} />
          {Object.keys(risk).some((key) => key !== "rules") && <KeyValueGrid values={withoutKeys(risk, ["rules"])} />}
        </RuleSection>
      )}
    </div>
  );
}

function StrategyChanges({ data }: { data: StrategyCenterDetail }) {
  const changes = data.diff.changes;
  if (!data.diff.base_version_id) {
    return <EmptyState icon={<GitCompareArrows size={30} />} title="这是最早可比较的快照" reason="没有更早的完整版本可以作为对照" />;
  }
  if (changes.length === 0) {
    return <EmptyState icon={<CheckCircle2 size={30} />} title="没有结构化规则变化" reason="与上一份可用快照的运行契约一致" />;
  }
  return (
    <div className="strategy-diff">
      <div className="strategy-diff__summary">
        <GitCompareArrows size={18} />
        <span>相对上一可用版本共 {changes.length} 处变化</span>
        <code>{shortId(data.diff.base_version_id, 16)} → {shortId(data.diff.target_version_id, 16)}</code>
      </div>
      <div className="strategy-diff__list">
        {changes.map((change, index) => <ChangeRow key={`${change.path}-${index}`} change={change} />)}
      </div>
    </div>
  );
}

function ChangeRow({ change }: { change: StrategyVersionChange }) {
  const label = change.change_type === "added" ? "新增" : change.change_type === "removed" ? "删除" : "调整";
  return (
    <div className={`strategy-change strategy-change--${change.change_type}`}>
      <div className="strategy-change__path"><span>{label}</span><code>{humanizePath(change.path)}</code></div>
      <div className="strategy-change__values">
        {change.change_type !== "added" && <ValueBlock label="之前" value={change.before} />}
        {change.change_type !== "removed" && <ValueBlock label="现在" value={change.after} />}
      </div>
    </div>
  );
}

function ValueBlock({ label, value }: { label: string; value: unknown }) {
  return <div><span>{label}</span><pre>{formatValue(value)}</pre></div>;
}

function StrategySnapshot({ data }: { data: StrategyCenterDetail }) {
  return (
    <div className="strategy-raw">
      <div className="strategy-raw__notice">
        <Clock3 size={16} /> 这是归档时保存的不可变内容，不会随当前文件变化。
      </div>
      <pre>{JSON.stringify(data.snapshot, null, 2)}</pre>
    </div>
  );
}

function RuleSection({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return <section className="strategy-rule-section"><header><h2>{title}</h2>{description && <p>{description}</p>}</header>{children}</section>;
}

function RuleColumns({ groups }: { groups: Array<{ title: string; items: unknown[]; tone: string }> }) {
  const visible = groups.filter((group) => group.items.length > 0);
  if (visible.length === 0) return <p className="strategy-empty-note">此版本未配置该组规则。</p>;
  return <div className="strategy-rule-columns">{visible.map((group) => <div key={group.title} className={`strategy-rule-group strategy-rule-group--${group.tone}`}><h3>{group.title}<span>{group.items.length}</span></h3>{group.items.map((item, index) => <RuleExpression key={index} value={item} />)}</div>)}</div>;
}

function RuleExpression({ value }: { value: unknown }) {
  if (typeof value === "string") return <code className="strategy-expression">{value}</code>;
  const row = asRecord(value);
  if (!row) return <code className="strategy-expression">{formatValue(value)}</code>;
  return <div className="strategy-expression-card"><code>{formatScalar(row.when ?? row.rule ?? value)}</code>{row.reason != null && <span>{formatScalar(row.reason)}</span>}</div>;
}

function ObjectCards({ title, items }: { title: string; items: unknown[] }) {
  if (items.length === 0) return null;
  return <RuleSection title={title} description={`共 ${items.length} 项，按配置顺序执行。`}><div className="strategy-object-cards">{items.map((item, index) => { const row = asRecord(item) || {}; return <article key={index}><h3>{formatScalar(row.name ?? `第 ${index + 1} 项`)}</h3><ObjectFields row={row} /></article>; })}</div></RuleSection>;
}

function ObjectTable({ items }: { items: unknown[] }) {
  if (items.length === 0) return <p className="strategy-empty-note">此版本未配置规则。</p>;
  return <div className="strategy-object-table">{items.map((item, index) => <div key={index} className="strategy-object-table__row"><span>{index + 1}</span><ObjectFields row={asRecord(item) || { value: item }} /></div>)}</div>;
}

function ObjectFields({ row }: { row: Record<string, unknown> }) {
  return <div className="strategy-object-fields">{Object.entries(row).filter(([key]) => key !== "name").map(([key, value]) => <div key={key}><span>{fieldLabel(key)}</span>{key === "when" ? <code>{formatScalar(value)}</code> : <strong>{formatValue(value)}</strong>}</div>)}</div>;
}

function KeyValueGrid({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(([, value]) => value != null);
  return <div className="strategy-kv-grid">{entries.map(([key, value]) => <div key={key}><span>{fieldLabel(key)}</span><strong>{formatValue(value)}</strong></div>)}</div>;
}

const FIELD_LABELS: Record<string, string> = {
  when: "条件", reason: "原因", delta: "分数变化", score_delta: "形态加分", score_cap: "最高分", hard_veto: "硬否决",
  risk_flags: "风险标记", entry_rule: "入场规则", invalid_rule: "失效规则", base: "基础分", weight: "策略权重",
  holding_days: "持仓周期", style: "策略风格", version: "规则版本", strategy_independence: "策略独立度",
  author: "维护者", default_preset_groups: "默认预设组", flag: "风险标记", level: "风险等级",
};

const fieldLabel = (key: string) => FIELD_LABELS[key] || key;
const asRecord = (value: unknown): Record<string, unknown> | null => value != null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const asArray = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const withoutKeys = (value: Record<string, unknown>, keys: string[]) => Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key)));

function formatValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return JSON.stringify(value, null, 2);
}

const formatScalar = (value: unknown) => value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
const shortId = (value: string, length: number) => value.length <= length ? value : `${value.slice(0, length)}…`;
const formatDateTime = (value: string | null) => formatBj(value, "datetime-sec", "时间未知");
const humanizePath = (path: string) => path.split(".").map((part) => FIELD_LABELS[part] || part).join(" › ");
