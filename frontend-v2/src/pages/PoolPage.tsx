import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  Clock,
  Loader2,
  PlayCircle,
  Search,
  XCircle,
} from "lucide-react";
import { api } from "../api/client";
import { useQuery } from "@tanstack/react-query";
import type { CockpitRecommendation, OutcomeSummary, RunSummary } from "../types";
import { ErrorPage } from "../components/StateViews";
import { PoolExpandedRow } from "../components/PoolExpandedRow";
import { StockTicker } from "../components/StockTicker";
import { LlmStanceBadge } from "../components/LlmStanceBadge";
import { MultiStrategyBadge } from "../components/MultiStrategyBadge";
import { useUrlRoute } from "../app/useUrlRoute";
import { useRunsQuery } from "../api/queries/useRunQueries";
import { useApi } from "../app/useApi";
import { queryParam } from "../app/nav";
import { describePoolRun, formatPoolRunTime, poolRunDate, summarizePoolRuns, type PoolRunKind } from "./poolRunView";
import { formatBj } from "../utils/time";

/**
 * 今日记录（原候选池）—— 日期运行日志 → 运行抽屉 → 股票二级展开。
 *
 * 运行记录是页面骨架；推荐股票是某次运行的结果。这样即使推荐数为 0，
 * 页面仍能明确展示本次运行的时间、预设、完成状态和筛空信息。
 */
type RiskLevel = CockpitRecommendation["risk_level"];
type RunFilter = "all" | PoolRunKind;

export function PoolPage() {
  const { navigate } = useUrlRoute();
  const runId = queryParam("run_id");
  const today = localDateKey();
  const [selectedDate, setSelectedDate] = useState(today);
  const [presetFilter, setPresetFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<RunFilter>("all");
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(new Set());
  const [experimentsOpen, setExperimentsOpen] = useState(false);
  const initializedDate = useRef("");
  const { data: runs, isLoading: loading, error, refetch } = useRunsQuery(100, true); // slim：列表不需要 result_summary；空票抽屉按需取详情

  const allRuns = runs ?? [];
  const linkedRun = runId ? allRuns.find((run) => run.run_id === runId) : undefined;
  const effectiveDate = selectedDate;
  const availableDates = useMemo(
    () => Array.from(new Set([today, ...allRuns.map(poolRunDate).filter(Boolean)])).sort().reverse(),
    [allRuns, today],
  );
  const dayRuns = useMemo(
    () => allRuns
      .filter((run) => poolRunDate(run) === effectiveDate)
      .sort((a, b) => runSortTime(b).localeCompare(runSortTime(a))),
    [allRuns, effectiveDate],
  );
  const presetOptions = useMemo(
    () => Array.from(new Set(dayRuns.map((run) => run.preset_name || run.run_name || "未命名预设"))).sort(),
    [dayRuns],
  );
  const filteredRuns = dayRuns.filter((run) => {
    if (presetFilter !== "all" && (run.preset_name || run.run_name || "未命名预设") !== presetFilter) return false;
    if (statusFilter !== "all" && describePoolRun(run).kind !== statusFilter) return false;
    return true;
  });
  // 曝光补跑（experiment）是内部补样机制（不进记录中心），与正式（formal）推荐混排会把
  // 「今天有没有正式推荐」淹没。拆分：formal 逐个展示，experiment 合并折叠进一张分组卡。
  const formalRuns = filteredRuns.filter((run) => run.run_mode !== "experiment");
  const experimentRuns = filteredRuns.filter((run) => run.run_mode === "experiment");
  const summary = summarizePoolRuns(dayRuns);
  const dateIndex = availableDates.indexOf(effectiveDate);
  const olderDate = dateIndex >= 0 ? availableDates[dateIndex + 1] : undefined;
  const newerDate = dateIndex > 0 ? availableDates[dateIndex - 1] : undefined;
  useEffect(() => {
    if (!linkedRun) return;
    setSelectedDate(poolRunDate(linkedRun));
    setExpandedRuns((current) => new Set(current).add(linkedRun.run_id));
  }, [linkedRun?.run_id, linkedRun?.trade_date]);

  useEffect(() => {
    if (!runs || initializedDate.current === effectiveDate) return;
    const defaults = dayRuns.slice(0, 1).map((run) => run.run_id);
    setExpandedRuns(new Set(defaults));
    initializedDate.current = effectiveDate;
  }, [runs, dayRuns, effectiveDate]);

  const chooseDate = (date: string) => {
    initializedDate.current = "";
    setPresetFilter("all");
    setStatusFilter("all");
    setSelectedDate(date);
  };

  const toggleRun = (id: string) => {
    setExpandedRuns((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        <div className="skeleton" style={{ height: 96 }} />
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 68 }} />
        ))}
      </div>
    );
  }
  if (error) return <ErrorPage title="运行记录加载失败" message={error.message} onRetry={() => void refetch()} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      <PoolRunToolbar
        date={effectiveDate}
        today={today}
        olderDate={olderDate}
        newerDate={newerDate}
        presetFilter={presetFilter}
        presetOptions={presetOptions}
        statusFilter={statusFilter}
        summary={summary}
        onDateChange={chooseDate}
        onPresetChange={setPresetFilter}
        onStatusChange={setStatusFilter}
      />

      {dayRuns.length === 0 ? (
        <NoRunsToday date={effectiveDate} today={today} onRun={() => navigate("/run")} />
      ) : filteredRuns.length === 0 ? (
        <div className="panel" style={{ padding: "var(--space-6)", textAlign: "center" }}>
          <div style={{ fontWeight: 600, color: "var(--ink)" }}>没有符合当前筛选的运行记录</div>
          <div className="text-muted" style={{ marginTop: "var(--space-2)", fontSize: "var(--text-sm)" }}>
            当天实际运行了 {dayRuns.length} 次，清除预设或状态筛选即可查看。
          </div>
        </div>
      ) : (
        <>
          {formalRuns.map((run) => (
            <RunDrawer
              key={run.run_id}
              run={run}
              open={expandedRuns.has(run.run_id)}
              onToggle={() => toggleRun(run.run_id)}
            />
          ))}
          {experimentRuns.length > 0 && (
            <div className="panel" style={{ padding: "var(--space-3) var(--space-4)" }}>
              <button
                onClick={() => setExperimentsOpen((o) => !o)}
                style={{
                  display: "flex", alignItems: "center", gap: "var(--space-2)", width: "100%",
                  background: "transparent", border: "none", cursor: "pointer", padding: 0, textAlign: "left",
                }}
              >
                {experimentsOpen ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
                <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                  曝光补跑 · {experimentRuns.length} 次（内部补样，不产生正式推荐）
                </span>
              </button>
              {experimentsOpen && (
                <div style={{ marginTop: "var(--space-3)", display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
                  {experimentRuns.map((run) => (
                    <RunDrawer
                      key={run.run_id}
                      run={run}
                      open={expandedRuns.has(run.run_id)}
                      onToggle={() => toggleRun(run.run_id)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function PoolRunToolbar({
  date,
  today,
  olderDate,
  newerDate,
  presetFilter,
  presetOptions,
  statusFilter,
  summary,
  onDateChange,
  onPresetChange,
  onStatusChange,
}: {
  date: string;
  today: string;
  olderDate?: string;
  newerDate?: string;
  presetFilter: string;
  presetOptions: string[];
  statusFilter: RunFilter;
  summary: ReturnType<typeof summarizePoolRuns>;
  onDateChange: (date: string) => void;
  onPresetChange: (value: string) => void;
  onStatusChange: (value: RunFilter) => void;
}) {
  return (
    <div className="panel" style={{ padding: "var(--space-4)", display: "grid", gap: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
        <CalendarDays size={18} className="text-muted" />
        <button className="btn btn--ghost" disabled={!olderDate} onClick={() => olderDate && onDateChange(olderDate)} title="前一个有记录的日期">
          <ChevronLeft size={16} />
        </button>
        <strong className="text-mono" style={{ fontSize: "var(--text-lg)", color: "var(--ink)" }}>
          {date} {date === today ? "今天" : ""}
        </strong>
        <button className="btn btn--ghost" disabled={!newerDate} onClick={() => newerDate && onDateChange(newerDate)} title="后一个有记录的日期">
          <ChevronRight size={16} />
        </button>
        <div style={{ display: "flex", gap: "var(--space-2)", marginLeft: "auto", flexWrap: "wrap" }}>
          <select value={presetFilter} onChange={(event) => onPresetChange(event.target.value)} style={selectStyle} aria-label="按预设筛选">
            <option value="all">全部预设</option>
            {presetOptions.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <select value={statusFilter} onChange={(event) => onStatusChange(event.target.value as RunFilter)} style={selectStyle} aria-label="按运行状态筛选">
            <option value="all">全部状态</option>
            <option value="completed">有推荐</option>
            <option value="empty">无推荐</option>
            <option value="active">运行中</option>
            <option value="failed">失败或中断</option>
          </select>
        </div>
      </div>
      <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--ink)", marginRight: "var(--space-2)" }}>{date === today ? "今日" : "当日"}运行 {summary.total} 次</strong>
        <SummaryTag icon={<CheckCircle2 size={13} />} label={`有推荐 ${summary.completed}`} tone="positive" />
        <SummaryTag icon={<AlertTriangle size={13} />} label={`无推荐 ${summary.empty}`} tone="warning" />
        <SummaryTag icon={<CircleDashed size={13} />} label={`运行中 ${summary.active}`} tone="accent" />
        <SummaryTag icon={<XCircle size={13} />} label={`失败 ${summary.failed}`} tone="negative" />
      </div>
    </div>
  );
}

const selectStyle = {
  background: "var(--surface-soft)",
  border: "1px solid var(--line-strong)",
  borderRadius: "var(--radius-md)",
  padding: "var(--space-2) var(--space-3)",
  color: "var(--ink)",
  fontFamily: "inherit",
} as const;

function SummaryTag({ icon, label, tone }: { icon: React.ReactNode; label: string; tone: "positive" | "warning" | "accent" | "negative" }) {
  const colors = {
    positive: "var(--positive)",
    warning: "var(--warning)",
    accent: "var(--accent-strong)",
    negative: "var(--danger)",
  };
  return (
    <span className="tag" style={{ color: colors[tone], display: "inline-flex", alignItems: "center", gap: 5 }}>
      {icon}{label}
    </span>
  );
}

function NoRunsToday({ date, today, onRun }: { date: string; today: string; onRun: () => void }) {
  return (
    <div className="panel" style={{ padding: "var(--space-6)", display: "flex", gap: "var(--space-4)", alignItems: "flex-start", flexWrap: "wrap" }}>
      <Clock size={28} className="text-muted" />
      <div style={{ flex: "1 1 360px" }}>
        <h3 style={{ margin: 0, color: "var(--ink)" }}>{date === today ? "今天尚未运行推荐" : "当天没有运行记录"}</h3>
        <p className="text-muted" style={{ margin: "var(--space-2) 0 0", lineHeight: 1.7 }}>
          {date === today
            ? "系统没有发现今天的推荐运行记录。这表示尚未运行，不是运行成功但没有推荐。"
            : `${date} 没有发现推荐运行记录。`}
        </p>
      </div>
      {date === today && <button className="btn btn--primary" onClick={onRun}><PlayCircle size={15} />去运行推荐</button>}
    </div>
  );
}

function RunDrawer({ run, open, onToggle }: { run: RunSummary; open: boolean; onToggle: () => void }) {
  const description = describePoolRun(run);
  const statusColor = runStatusColor(description.kind);
  return (
    <section style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{ width: "100%", display: "flex", alignItems: "center", gap: "var(--space-4)", flexWrap: "wrap", padding: "var(--space-4)", background: "transparent", border: 0, color: "var(--ink)", textAlign: "left", cursor: "pointer" }}
      >
        {open ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
        <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", minWidth: 150 }}>
          <strong className="text-mono" style={{ fontSize: "var(--text-xl)" }}>{formatPoolRunTime(run.started_at, run.trade_date)}</strong>
          <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>开始</span>
        </div>
        {run.trade_date && run.trade_date !== poolRunDate(run) && <span className="tag text-mono" style={{ fontSize: "var(--text-xs)" }}>数据 {run.trade_date}</span>}
        <div style={{ minWidth: 180, flex: "1 1 220px" }}>
          <strong>{run.preset_name || run.run_name || "未命名预设"}</strong>
          {run.run_name && run.run_name !== run.preset_name && <div className="text-muted" style={{ fontSize: "var(--text-xs)", marginTop: 3 }}>{run.run_name}</div>}
        </div>
        <span className="tag" style={{ color: statusColor, borderColor: statusColor, display: "inline-flex", alignItems: "center", gap: 6 }}>
          <RunStatusIcon kind={description.kind} />{description.label}
          {description.kind === "active" && run.progress_pct != null ? ` ${Math.round(run.progress_pct)}%` : ""}
        </span>
        <strong className="text-mono" style={{ color: description.kind === "empty" ? "var(--warning)" : "var(--ink)", minWidth: 84, textAlign: "right" }}>
          {description.countLabel}
        </strong>
      </button>
      {open && <RunDrawerBody run={run} kind={description.kind} />}
    </section>
  );
}

function RunStatusIcon({ kind }: { kind: PoolRunKind }) {
  if (kind === "completed") return <CheckCircle2 size={14} />;
  if (kind === "empty") return <AlertTriangle size={14} />;
  if (kind === "active") return <Loader2 size={14} className="spin" />;
  if (kind === "failed") return <XCircle size={14} />;
  return <CircleDashed size={14} />;
}

function RunDrawerBody({ run, kind }: { run: RunSummary; kind: PoolRunKind }) {
  const { navigate } = useUrlRoute();
  const detailButton = (
    <button className="btn btn--ghost" onClick={() => navigate("/records", { params: { tab: "run", run_id: run.run_id } })}>
      查看本次运行详情 <ChevronRight size={14} />
    </button>
  );
  // 列表为 slim（无 result_summary）：空票抽屉按需拉运行详情取同一份 result_summary
  const detailQ = useQuery({ queryKey: ["runs", run.run_id], queryFn: () => api.runDetail(run.run_id), enabled: kind === "empty" && !run.result_summary });
  const summary = (run.result_summary ?? detailQ.data?.result_summary) as RunSummary["result_summary"] | undefined;
  if (kind === "completed") return <RecommendationResults run={run} />;
  if (kind === "empty") {
    const eligible = summary?.base_filter_eligible_count;
    const strategyInput = summary?.skill_input_count ?? summary?.actual_scan_count;
    const marketTotal = summary?.unified_quote_total ?? run.universe_count;
    return (
      <div style={drawerBodyStyle}>
        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-start" }}>
          <CheckCircle2 size={20} style={{ color: "var(--positive)", flexShrink: 0 }} />
          <div>
            <strong style={{ color: "var(--ink)" }}>推荐管线已正常执行完成</strong>
            <p className="text-muted" style={{ margin: "var(--space-1) 0 0", lineHeight: 1.7 }}>本次没有股票达到推荐条件，不是运行失败。</p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <FunnelStep label="全市场" value={marketTotal} />
          <ChevronRight size={14} className="text-muted" />
          <FunnelStep label="可交易" value={eligible} />
          <ChevronRight size={14} className="text-muted" />
          <FunnelStep label="进入策略" value={strategyInput} />
          <ChevronRight size={14} className="text-muted" />
          <FunnelStep label="达到推荐线" value={0} warning />
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>{detailButton}</div>
      </div>
    );
  }
  if (kind === "active") {
    const pct = Math.max(0, Math.min(100, run.progress_pct ?? 0));
    return (
      <div style={drawerBodyStyle}>
        <div><strong style={{ color: "var(--ink)" }}>正在进行：{run.progress_message || run.progress_phase || "等待后台更新"}</strong></div>
        <div style={{ height: 8, borderRadius: 999, background: "var(--surface-soft)", overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent)", transition: "width 240ms ease" }} />
        </div>
        <div className="text-muted" style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)", flexWrap: "wrap", fontSize: "var(--text-sm)" }}>
          <span>{run.progress_current ?? 0} / {run.progress_total ?? "—"}</span>
          <span>{run.heartbeat_at ? `最后更新 ${formatPoolRunTime(run.heartbeat_at)}` : "等待首次进度回报"}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>{detailButton}</div>
      </div>
    );
  }
  if (kind === "failed") {
    return (
      <div style={drawerBodyStyle}>
        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-start" }}>
          <XCircle size={20} style={{ color: "var(--danger)", flexShrink: 0 }} />
          <div>
            <strong style={{ color: "var(--ink)" }}>本次运行未完成，因此没有形成有效推荐结果。</strong>
            <p style={{ margin: "var(--space-2) 0 0", color: "var(--danger)", lineHeight: 1.7 }}>{shortError(run.error_message || run.progress_message)}</p>
            {run.progress_phase && <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>停止阶段：{run.progress_phase}</div>}
          </div>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)", flexWrap: "wrap" }}>
          {detailButton}
          <button className="btn btn--primary" onClick={() => navigate("/run", { params: { preset_id: run.preset_id } })}>按原预设重新运行</button>
        </div>
      </div>
    );
  }
  return <div style={drawerBodyStyle}><span className="text-muted">当前状态暂时无法识别，请打开运行详情确认。</span>{detailButton}</div>;
}

const drawerBodyStyle = {
  borderTop: "1px solid var(--line)",
  padding: "var(--space-4)",
  display: "grid",
  gap: "var(--space-4)",
  background: "var(--surface-soft)",
} as const;

function FunnelStep({ label, value, warning = false }: { label: string; value?: number; warning?: boolean }) {
  return (
    <div className="tag" style={{ padding: "var(--space-2) var(--space-3)", color: warning ? "var(--warning)" : "var(--ink)" }}>
      <span className="text-muted">{label}</span> <strong className="text-mono">{value ?? "—"}</strong>
    </div>
  );
}

function RecommendationResults({ run }: { run: RunSummary }) {
  const { navigate, navigateSection } = useUrlRoute();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<{ risk?: RiskLevel; q: string }>({ q: "" });
  const { data: items, loading, error, refetch } = useApi<CockpitRecommendation[]>(
    () => api.runRecommendations(run.run_id).then((list) => list as unknown as CockpitRecommendation[]),
    [run.run_id],
  );
  const filtered = useMemo(() => {
    const q = filter.q.trim().toLowerCase();
    return (items ?? []).filter((item) => {
      if (filter.risk && item.risk_level !== filter.risk) return false;
      return !q || `${item.code} ${item.name}`.toLowerCase().includes(q);
    });
  }, [items, filter]);
  const codes = useMemo(() => Array.from(new Set((items ?? []).map((item) => item.code))), [items]);
  const codesKey = codes.join(",");
  const { data: outcomeSummary } = useApi<Record<string, OutcomeSummary>>(
    () => codes.length ? api.recommendationOutcomeSummary(codes) : Promise.resolve({}),
    [codesKey],
  );

  if (loading) return <div style={drawerBodyStyle}><div className="skeleton" style={{ height: 56 }} /><div className="skeleton" style={{ height: 56 }} /></div>;
  if (error) return <div style={drawerBodyStyle}><ErrorPage title="推荐股票加载失败" message={error} onRetry={refetch} /></div>;
  if (!items || items.length === 0) {
    return <div style={drawerBodyStyle}><AlertTriangle size={20} style={{ color: "var(--warning)" }} /><span>运行记录显示 {run.recommended_count} 只推荐，但没有读取到股票明细。</span></div>;
  }

  const toggleStock = (id: string) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div style={drawerBodyStyle}>
      <FilterBar total={items.length} filtered={filtered.length} filter={filter} onChange={setFilter} />
      <SectorDiversityBar items={filtered} />
      {filtered.map((item, index) => {
        const isOpen = expanded.has(item.recommendation_id);
        return (
          <div key={item.recommendation_id} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
            <button onClick={() => toggleStock(item.recommendation_id)} style={{ width: "100%", display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap", padding: "var(--space-3) var(--space-4)", background: "transparent", border: "none", textAlign: "left", cursor: "pointer" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0, flex: "1 1 260px" }}>
                <span className="text-mono text-muted" style={{ fontSize: "var(--text-sm)", flexShrink: 0 }}>#{index + 1}</span>
                <StockTicker code={item.code} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, color: "var(--ink)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    {item.name}
                    {item.industry && <span className="tag" style={{ fontSize: "var(--text-xs)", flexShrink: 0 }}>{item.industry}</span>}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                    {item.entry_rule && <RuleLine label="入场" text={item.entry_rule} />}
                    {item.invalid_rule && <RuleLine label="失效" text={item.invalid_rule} />}
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap", marginLeft: "auto" }}>
                <TradeParamsInline item={item} />
                <span className="text-mono" style={{ fontWeight: 700, color: "var(--accent-strong)", fontSize: "var(--text-lg)" }}>{item.final_score.toFixed(0)}</span>
                <LlmStanceBadge stance={item.llm_stance} delta={item.llm_delta} />
                <MultiStrategyBadge skillBreakdown={item.skill_breakdown} />
                <RiskTag level={item.risk_level} setup={item.setup_type} />
                {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </div>
            </button>
            {isOpen && <PoolExpandedRow item={item} outcomeSummary={outcomeSummary ?? {}} onReplay={() => navigate("/history", { params: { recommendation_id: item.recommendation_id } })} onViewHistory={() => navigateSection("history")} />}
          </div>
        );
      })}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button className="btn btn--ghost" onClick={() => navigate("/records", { params: { tab: "run", run_id: run.run_id } })}>查看本次运行详情 <ChevronRight size={14} /></button>
      </div>
    </div>
  );
}

function localDateKey() {
  return formatBj(new Date().toISOString(), "date");
}

function runSortTime(run: RunSummary) {
  return run.started_at || run.finished_at || `${run.trade_date}T00:00:00`;
}

function runStatusColor(kind: PoolRunKind) {
  if (kind === "completed") return "var(--positive)";
  if (kind === "empty") return "var(--warning)";
  if (kind === "active") return "var(--accent-strong)";
  if (kind === "failed") return "var(--danger)";
  return "var(--muted)";
}

function shortError(message?: string | null) {
  if (!message) return "未记录具体失败原因";
  const compact = message.replace(/\s+/g, " ").trim();
  return compact.length > 240 ? `${compact.slice(0, 240)}…` : compact;
}

/** 板块分散度指示器 —— 显示行业分布，过度集中时警告 */
function SectorDiversityBar({ items }: { items: CockpitRecommendation[] }) {
  if (items.length === 0) return null;

  const industryCount: Record<string, number> = {};
  for (const c of items) {
    const ind = c.industry || "未知";
    industryCount[ind] = (industryCount[ind] || 0) + 1;
  }

  const industries = Object.entries(industryCount).sort((a, b) => b[1] - a[1]);
  const maxCount = industries[0]?.[1] || 0;
  const maxRatio = maxCount / items.length;
  const isConcentrated = maxRatio > 0.5 && items.length > 2;

  return (
    <div
      className="panel"
      style={{
        padding: "var(--space-2) var(--space-4)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        flexWrap: "wrap",
        fontSize: "var(--text-sm)",
      }}
    >
      <span className="text-muted">板块分散度</span>
      <span className="text-mono" style={{ fontWeight: 600 }}>
        {items.length} 只 / {industries.length} 行业
      </span>
      {isConcentrated && (
        <span
          className="tag tag--warning"
          style={{ fontSize: "var(--text-xs)" }}
          title={`${industries[0][0]} 占比 ${(maxRatio * 100).toFixed(0)}%，过度集中`}
        >
          ⚠ {industries[0][0]} {maxCount}/{items.length} ({(maxRatio * 100).toFixed(0)}%)
        </span>
      )}
    </div>
  );
}

function FilterBar({
  total,
  filtered,
  filter,
  onChange,
}: {
  total: number;
  filtered: number;
  filter: { risk?: RiskLevel; q: string };
  onChange: (f: { risk?: RiskLevel; q: string }) => void;
}) {
  const riskOptions: Array<{ label: string; value?: RiskLevel }> = [
    { label: "全部" },
    { label: "低风险", value: "low" },
    { label: "中风险", value: "medium" },
    { label: "高风险", value: "high" },
  ];
  return (
    <div
      className="panel"
      style={{
        padding: "var(--space-3) var(--space-4)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-3)",
        flexWrap: "wrap",
      }}
    >
      <Search size={16} className="text-muted" />
      <input
        placeholder="搜索代码或名称…"
        value={filter.q}
        onChange={(e) => onChange({ ...filter, q: e.target.value })}
        style={{
          background: "var(--surface-soft)",
          border: "1px solid var(--line-strong)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-2) var(--space-3)",
          color: "var(--ink)",
          fontSize: "var(--text-sm)",
          minWidth: 200,
          fontFamily: "inherit",
        }}
      />
      <div style={{ display: "flex", gap: "var(--space-1)" }}>
        {riskOptions.map((r) => (
          <button
            key={r.label}
            className={`btn ${filter.risk === r.value ? "btn--primary" : "btn--ghost"}`}
            onClick={() => onChange({ ...filter, risk: r.value })}
            style={{ padding: "var(--space-1) var(--space-3)", fontSize: "var(--text-sm)" }}
          >
            {r.label}
          </button>
        ))}
      </div>
      <span className="text-muted" style={{ marginLeft: "auto", fontSize: "var(--text-sm)" }}>
        {filtered} / {total}
      </span>
    </div>
  );
}

function RiskTag({ level, setup }: { level: RiskLevel; setup?: string | null }) {
  const toneClass =
    level === "high" ? "tag--negative" : level === "medium" ? "tag--warning" : "tag--positive";
  const color =
    level === "high" ? "var(--market-up)" : level === "medium" ? "var(--warning)" : "var(--market-down)";
  return (
    <div style={{ display: "flex", gap: "var(--space-1)" }}>
      {setup && <span className="tag">{setup}</span>}
      <span className={`tag ${toneClass}`} style={{ color }}>{level}</span>
    </div>
  );
}

/**
 * 行内决策规则（入场 / 失效）—— 折叠态即可见完整决策链，无需逐行展开。
 * 标签用 ink-secondary 提亮便于扫读，规则文本沿用 muted 保持克制（bold 只留给关键数字）。
 * 长规则单行省略（title 悬浮看全文），避免折叠行被撑高。
 */
function RuleLine({ label, text }: { label: string; text: string }) {
  return (
    <div
      style={{
        fontSize: "var(--text-xs)",
        color: "var(--text-muted)",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        display: "flex",
        gap: "var(--space-1)",
      }}
      title={text}
    >
      <span style={{ flexShrink: 0, color: "var(--ink-secondary)", fontWeight: 600 }}>{label} ·</span>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{text}</span>
    </div>
  );
}

/**
 * 行内交易参数 —— 推荐价/止损/止盈一屏可见，等宽数字右对齐。
 * A 股涨红跌绿：止盈(上行目标)=红、止损(下行保护)=绿，低饱和。
 * 让用户在展开前即可看到风控报价，无需逐行点开。
 */
function TradeParamsInline({ item }: { item: CockpitRecommendation }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "var(--space-3)" }}>
      <TradeParam label="推荐" value={item.recommend_price} />
      <TradeParam label="止损" value={item.stop_loss} tone="down" />
      <TradeParam label="止盈" value={item.take_profit} tone="up" />
    </div>
  );
}

function TradeParam({
  label,
  value,
  tone,
}: {
  label: string;
  value?: number | null;
  tone?: "up" | "down";
}) {
  const color =
    value == null
      ? "var(--text-muted)"
      : tone === "up"
        ? "var(--market-up)"
        : tone === "down"
          ? "var(--market-down)"
          : "var(--ink)";
  return (
    <div style={{ display: "inline-flex", alignItems: "baseline", gap: 4 }}>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono, monospace)", fontSize: "var(--text-sm)", fontWeight: 600, color }}>
        {value != null ? value.toFixed(2) : "—"}
      </span>
    </div>
  );
}
