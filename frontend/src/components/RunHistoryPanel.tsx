import {
  Flame,
  Layers3,
  Play,
  Target,
  Trash2,
  X,
} from "lucide-react";
import { memo, useState } from "react";
import type { RunSummary } from "../types";

type Props = {
  runs: RunSummary[];
  runHistoryMode: RunHistoryMode;
  onModeChange: (mode: RunHistoryMode) => void;
  selectedRunId: string;
  onSelect: (run: RunSummary) => void;
  onOpenPool: (run: RunSummary) => void;
  onDelete: (runId: string) => void;
  onResume: (runId: string) => void;
};

export type RunHistoryMode = "all" | "formal" | "experiment";

export default memo(function RunHistoryPanel({
  runs,
  runHistoryMode,
  onModeChange,
  selectedRunId,
  onSelect,
  onOpenPool,
  onDelete,
  onResume,
}: Props) {
  const [modalOpen, setModalOpen] = useState(false);
  const [modalRun, setModalRun] = useState<RunSummary | null>(null);

  const visibleRuns = runs.filter(
    (run) => runHistoryMode === "all" || run.run_mode === runHistoryMode,
  );
  const currentModalRun = modalRun ? runs.find((run) => run.run_id === modalRun.run_id) ?? modalRun : null;

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
              const isActive = run.status === "queued" || run.status === "running";
              const isFailed = run.status === "failed";
              const isInterrupted = run.status === "interrupted";
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
                      <Tag text={run.is_stale ? "stale" : run.status} />
                      {isInterrupted || isFailed ? (
                        <button
                          className="icon-btn primary"
                          title="从中断点继续执行"
                          onClick={(e) => { e.stopPropagation(); onResume(run.run_id); }}
                          style={{ marginLeft: "auto" }}
                        >
                          <Play size={14} />
                        </button>
                      ) : null}
                      <button
                        className="icon-btn danger"
                        title="删除运行记录"
                        onClick={(e) => { e.stopPropagation(); onDelete(run.run_id); }}
                        style={{ marginLeft: isInterrupted || isFailed ? undefined : "auto" }}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <div className="run-meta">
                      <span>{run.trade_date}</span>
                      <span>{run.market_scope}</span>
                      <span>{run.skill_count} 个 Skill</span>
                    </div>
                  </div>
                  {isFailed || isInterrupted ? (
                    <div className="run-summary-line" style={{ color: "#dc2626" }}>
                      {run.error_message ? run.error_message.slice(0, 80) + (run.error_message.length > 80 ? "..." : "") : "未知错误"}
                      <RunProgress run={run} compact />
                    </div>
                  ) : isActive ? (
                    <div className="run-summary-line active-run-summary">
                      <RunProgress run={run} />
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

      {modalOpen && currentModalRun ? (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
          <section className="detail-modal run-modal" role="dialog" aria-modal="true" aria-label={`${currentModalRun.run_name} 摘要`} onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" aria-label="关闭摘要" onClick={() => setModalOpen(false)}>
              <X size={18} />
            </button>
            <RunDetailPane
              run={currentModalRun}
              onOpenPool={() => {
                setModalOpen(false);
                onOpenPool(currentModalRun);
              }}
            />
          </section>
        </div>
      ) : null}
    </div>
  );
});

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
      {(run.status === "queued" || run.status === "running" || run.status === "interrupted" || run.is_stale) ? (
        <div className="summary-block wide">
          <span>运行进度</span>
          <RunProgress run={run} />
        </div>
      ) : null}
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

function RunProgress({ run, compact = false }: { run: RunSummary; compact?: boolean }) {
  const pct = progressPercent(run);
  const message = run.progress_message || displayText(run.progress_phase || run.status);
  const count =
    (run.progress_total || 0) > 0
      ? `${run.progress_current || 0} / ${run.progress_total}`
      : displayText(run.progress_phase || run.status);
  return (
    <div className={compact ? "run-progress compact" : "run-progress"}>
      <div className="run-progress-head">
        <strong>{message}</strong>
        <span>{count}</span>
      </div>
      {(run.progress_total || 0) > 0 ? (
        <div className="run-progress-track" aria-label="运行进度">
          <div className="run-progress-fill" style={{ width: `${pct}%` }} />
        </div>
      ) : null}
      <div className="run-progress-meta">
        <span>{displayText(run.progress_phase || run.status)}</span>
        {run.heartbeat_age_seconds != null ? <span>心跳 {formatDuration(run.heartbeat_age_seconds)}前</span> : null}
        {run.is_stale ? <span className="danger-text">疑似中断</span> : null}
      </div>
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
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "运行失败",
    interrupted: "已中断",
    stale: "疑似中断",
    prepare: "准备运行",
    starting: "启动中",
    market_data: "市场数据",
    scope_filter: "范围过滤",
    auxiliary_data: "辅助数据",
    feature_build: "特征构建",
    rule_scan: "规则扫描",
    skill_gate: "规则门",
    skill_review: "Skill 深审",
    aggregate: "聚合",
    persist: "保存结果",
  };
  return labels[key] ?? key;
}

function displayTagText(text: string) {
  const labels: Record<string, string> = {
    formal: "正式",
    experiment: "实验",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "运行失败",
    interrupted: "已中断",
    stale: "疑似中断",
  };
  return displayText(text) || labels[text] || text;
}

function toneForTag(text: string) {
  if (["formal", "completed"].includes(text)) return "positive";
  if (["experiment", "queued", "running"].includes(text)) return "warning";
  if (["failed", "interrupted", "stale"].includes(text)) return "danger";
  return "neutral";
}

function progressPercent(run: RunSummary) {
  if (typeof run.progress_pct === "number") {
    return Math.max(0, Math.min(100, run.progress_pct * 100));
  }
  if (!run.progress_total) return 0;
  return Math.max(0, Math.min(100, ((run.progress_current || 0) / run.progress_total) * 100));
}

function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时`;
}
