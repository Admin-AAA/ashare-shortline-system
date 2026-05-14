import {
  Flame,
  Layers3,
  Target,
  X,
} from "lucide-react";
import { useState } from "react";
import type { RunSummary } from "../types";

type Props = {
  runs: RunSummary[];
  runHistoryMode: RunHistoryMode;
  onModeChange: (mode: RunHistoryMode) => void;
  selectedRunId: string;
  onSelect: (run: RunSummary) => void;
  onOpenPool: (run: RunSummary) => void;
};

export type RunHistoryMode = "all" | "formal" | "experiment";

export default function RunHistoryPanel({
  runs,
  runHistoryMode,
  onModeChange,
  selectedRunId,
  onSelect,
  onOpenPool,
}: Props) {
  const [modalOpen, setModalOpen] = useState(false);
  const [modalRun, setModalRun] = useState<RunSummary | null>(null);

  const visibleRuns = runs.filter(
    (run) => runHistoryMode === "all" || run.run_mode === runHistoryMode,
  );

  function handleSelect(run: RunSummary) {
    onSelect(run);
    setModalRun(run);
    setModalOpen(true);
  }

  return (
    <div className="run-workspace">
      <section className="panel run-filter-bar">
        <PanelTitle title="运行池" subtitle="按模式查看历史 Run" />
        <div className="mode-switch">
          <button className={runHistoryMode === "all" ? "active" : ""} onClick={() => onModeChange("all")}>
            <Layers3 size={14} />
            全部
          </button>
          <button className={runHistoryMode === "formal" ? "active" : ""} onClick={() => onModeChange("formal")}>
            <Target size={14} />
            正式
          </button>
          <button className={runHistoryMode === "experiment" ? "active" : ""} onClick={() => onModeChange("experiment")}>
            <Flame size={14} />
            实验
          </button>
        </div>
        <div className="mini-stat-row run-stat-row">
          <MiniStat label="Run 总数" value={visibleRuns.length} />
          <MiniStat label="正式" value={visibleRuns.filter((run) => run.run_mode === "formal").length} />
          <MiniStat label="实验" value={visibleRuns.filter((run) => run.run_mode === "experiment").length} />
          <MiniStat label="推荐合计" value={visibleRuns.reduce((sum, run) => sum + run.recommended_count, 0)} />
        </div>
      </section>

      <section className="panel run-table-panel">
        <PanelTitle title="运行记录" subtitle="点击 Run 查看摘要" />
        {visibleRuns.length ? (
          <div className="run-list" role="list">
            {visibleRuns.map((run) => {
              const isRunning = run.status === "running";
              const isFailed = run.status === "failed";
              return (
                <button
                  key={run.run_id}
                  className={`run-row ${run.run_id === selectedRunId ? "selected" : ""}`}
                  onClick={() => handleSelect(run)}
                  role="listitem"
                >
                  <div className="run-main">
                    <div className="run-title">
                      <strong>{run.run_name}</strong>
                      <Tag text={run.run_mode} />
                      {isRunning && <span className="tag" style={{ background: "#dbeafe", color: "#2563eb" }}>运行中</span>}
                      {isFailed && <span className="tag" style={{ background: "#fee2e2", color: "#dc2626" }}>运行失败</span>}
                    </div>
                    <div className="run-meta">
                      <span>{run.trade_date}</span>
                      <span>{run.market_scope}</span>
                      <span>{run.skill_count} 个 Skill</span>
                    </div>
                  </div>
                  {isFailed ? (
                    <div className="run-summary-line" style={{ color: "#dc2626" }}>
                      {run.error_message ? run.error_message.slice(0, 80) + (run.error_message.length > 80 ? "..." : "") : "未知错误"}
                    </div>
                  ) : isRunning ? (
                    <div className="run-summary-line" style={{ color: "#6b7280" }}>
                      处理中...
                    </div>
                  ) : (
                    <>
                      <div className="run-summary-line">
                        <span>输出概览</span>
                        <strong>
                          推荐 {run.recommended_count} / 股票池 {run.universe_count}
                        </strong>
                      </div>
                      <div className="run-score">
                        <span>胜率</span>
                        <strong>{(run.hit_rate * 100).toFixed(1)}%</strong>
                      </div>
                      <div className="run-score">
                        <span>均分</span>
                        <strong>{Number(run.result_summary.avg_score ?? 0).toFixed(1)}</strong>
                      </div>
                    </>
                  )}
                </button>
              );
            })}
          </div>
        ) : (
          <EmptyHint text="暂无运行记录" />
        )}
      </section>

      {modalOpen && modalRun ? (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
          <section className="detail-modal run-modal" role="dialog" aria-modal="true" aria-label={`${modalRun.run_name} 摘要`} onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" aria-label="关闭摘要" onClick={() => setModalOpen(false)}>
              <X size={18} />
            </button>
            <RunDetailPane
              run={modalRun}
              onOpenPool={() => {
                setModalOpen(false);
                onOpenPool(modalRun);
              }}
            />
          </section>
        </div>
      ) : null}
    </div>
  );
}

function PanelTitle({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
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
  const tone = toneForTag(text);
  return (
    <span className="tag" data-tone={tone}>
      {displayTagText(text)}
    </span>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="empty-hint">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
      <span>{text}</span>
    </div>
  );
}

function RunDetailPane({ run, onOpenPool }: { run: RunSummary; onOpenPool: () => void }) {
  return (
    <div className="detail-stack">
      <header className="detail-hero">
        <div className="detail-identity">
          <span className="eyebrow">运行摘要</span>
          <h2>{run.run_name}</h2>
          <div className="detail-meta">
            <strong>{run.trade_date}</strong>
            <Tag text={run.run_mode} />
            <span>{displayText(run.status)}</span>
          </div>
        </div>
        <div className="score-card">
          <span>胜率</span>
          <strong>{(run.hit_rate * 100).toFixed(1)}%</strong>
          <small>{run.recommended_count} 条推荐</small>
        </div>
      </header>
      <div className="detail-quick-actions">
        <button className="ghost-btn primary" onClick={onOpenPool}>
          <Layers3 size={14} />
          查看机会池
        </button>
      </div>
      <RunSummaryCard run={run} />
    </div>
  );
}

function RunSummaryCard({ run }: { run: RunSummary }) {
  const pipeline = run.result_summary.pipeline ?? {};
  const demoLimited = Boolean(run.result_summary.demo_limited ?? pipeline.demo_limited);
  const scanLimited = Boolean(run.result_summary.scan_limited ?? pipeline.scan_limited);
  const skillLimited = Boolean(run.result_summary.skill_limited ?? pipeline.skill_limited);
  return (
    <div className="summary-grid">
      <MiniStat label="日期" value={run.trade_date} />
      <MiniStat label="模式" value={displayText(run.run_mode)} />
      <MiniStat label="运行数据" value={displayText(run.result_summary.data_source_mode || "-")} />
      <MiniStat label="数据源" value={run.result_summary.data_source || "-"} />
      <MiniStat label="报价源" value={run.result_summary.quote_source || "-"} />
      <MiniStat label="Agent" value={run.result_summary.agent_runtime || "-"} />
      <MiniStat label="推荐数" value={run.recommended_count} />
      <MiniStat label="胜率" value={`${(run.hit_rate * 100).toFixed(1)}%`} />
      <MiniStat label="Skill 数" value={run.skill_count} />
      <MiniStat label="统一报价" value={run.result_summary.unified_quote_total ?? pipeline.unified_quote_total ?? "-"} />
      <MiniStat label="基础合格" value={run.result_summary.base_filter_eligible_count ?? pipeline.base_filter_eligible_count ?? "-"} />
      <MiniStat label="全市场日K" value={run.result_summary.daily_kline_coverage_count ?? pipeline.daily_kline_coverage_count ?? "-"} />
      <MiniStat label="合格日K" value={run.result_summary.filtered_kline_coverage_count ?? pipeline.filtered_kline_coverage_count ?? "-"} />
      <MiniStat label="实际扫描" value={run.result_summary.actual_scan_count ?? pipeline.actual_scan_count ?? run.universe_count} />
      <MiniStat label="Skill 输入" value={run.result_summary.skill_input_count ?? pipeline.skill_input_count ?? "-"} />
      {demoLimited ? (
        <div className="summary-block wide warning-block">
          <span>候选池模式</span>
          <strong>
            Demo 限流模式：
            {scanLimited ? "推荐扫描少于基础合格股票" : ""}
            {scanLimited && skillLimited ? "；" : ""}
            {skillLimited ? "Skill 输入少于实际扫描股票" : ""}
          </strong>
        </div>
      ) : null}
      <div className="summary-block wide">
        <span>Top Codes</span>
        <div className="pill-row">
          {(run.result_summary.top_codes || []).map((code) => (
            <span key={code} className="pill static">
              {code}
            </span>
          ))}
        </div>
      </div>
      <div className="summary-block wide">
        <span>K线源</span>
        <div className="pill-row">
          {Object.entries(run.result_summary.kline_sources || {}).map(([source, count]) => (
            <span key={source} className="pill static">
              {source} x {count}
            </span>
          ))}
        </div>
      </div>
      <SkillGateFunnel pipeline={pipeline} />
    </div>
  );
}

function SkillGateFunnel({ pipeline }: { pipeline: Record<string, unknown> }) {
  const gate = (pipeline.skill_gate as Record<string, unknown>) ?? {};
  const perSkill = (gate.per_skill as Record<string, Record<string, number>>) ?? {};
  const tiers = gate.quality_tiers as Record<string, string> | undefined;
  const skillIds = Object.keys(perSkill);
  if (!skillIds.length) return null;

  return (
    <div className="summary-block wide">
      <span>Skill 三级漏斗</span>
      <div style={{ fontSize: "0.75rem", color: "#6b7280", marginBottom: "0.5rem" }}>
        规则合格 → Gate 入围 → LLM 深审
        {tiers ? Object.entries(tiers).map(([tier, desc]) => (
          <span key={tier} style={{ marginLeft: "0.75rem" }}>
            <span className="tag" data-tone={tier === "high" ? "positive" : tier === "mid" ? "warning" : "neutral"}>
              {tier}
            </span>
            {" "}{desc}
          </span>
        )) : null}
      </div>
      <div className="funnel-table" style={{ display: "grid", gap: "0.375rem" }}>
        {skillIds.map((sid) => {
          const stats = perSkill[sid];
          const name = sid.replace(/-skill$/, "");
          return (
            <div key={sid} style={{
              display: "grid",
              gridTemplateColumns: "1fr 60px 60px 60px 40px",
              alignItems: "center",
              gap: "0.25rem",
              padding: "0.25rem 0.5rem",
              background: "#f9fafb",
              borderRadius: "4px",
              fontSize: "0.78rem",
            }}>
              <span style={{ fontWeight: 600 }}>{name}</span>
              <span style={{ color: "#6b7280", textAlign: "center" }}>
                <strong style={{ color: "#1f2937" }}>{stats.rule_eligible ?? 0}</strong>
              </span>
              <span style={{ color: "#6b7280", textAlign: "center" }}>
                <strong style={{ color: "#2563eb" }}>{stats.gate_pass ?? 0}</strong>
              </span>
              <span style={{ color: "#6b7280", textAlign: "center" }}>
                <strong style={{ color: "#059669" }}>{stats.llm_reviewed ?? 0}</strong>
              </span>
              <span style={{ textAlign: "right", fontSize: "0.7rem", color: "#9ca3af" }}>
                /{stats.dynamic_max_per_skill ?? "-"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function displayText(key: string): string {
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    local_pool: "仅本地池",
    local_pool_preferred: "本地池优先",
    realtime: "仅实时数据源",
    running: "运行中",
    completed: "已完成",
    failed: "运行失败",
  };
  return labels[key] ?? key;
}

function displayTagText(text: string) {
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    completed: "已完成",
    failed: "运行失败",
  };
  return displayText(text) || labels[text] || text;
}

function toneForTag(text: string) {
  if (["formal", "completed"].includes(text)) return "positive";
  if (["experiment"].includes(text)) return "warning";
  if (["failed"].includes(text)) return "danger";
  return "neutral";
}
