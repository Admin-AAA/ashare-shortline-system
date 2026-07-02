import { Layers3 } from "lucide-react";
import type { RunSummary } from "../types";
import {
  displayTagText,
  displayText,
  formatDateTime,
  formatDuration,
  progressPercent,
  toneForTag,
} from "./runHistoryHelpers";

export function PanelTitle({
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

export function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function Tag({ text }: { text: string }) {
  const tone = toneForTag(text);
  return (
    <span className="tag" data-tone={tone}>
      {displayTagText(text)}
    </span>
  );
}

export function EmptyHint({ text }: { text: string }) {
  return (
    <div className="empty-hint">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
      <span>{text}</span>
    </div>
  );
}

export function RunDetailPane({ run, onOpenPool }: { run: RunSummary; onOpenPool: () => void }) {
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

export function RunSummaryCard({ run }: { run: RunSummary }) {
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

export function RunProgress({ run, compact = false }: { run: RunSummary; compact?: boolean }) {
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

export function SkillGateFunnel({ pipeline }: { pipeline: Record<string, unknown> }) {
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
