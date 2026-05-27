import { memo } from "react";
import {
  PauseCircle,
  Play,
  RefreshCcw,
} from "lucide-react";
import type { DataSourceHealthRow } from "../types";
import { businessStatusLabel } from "../utils/display";

type Props = {
  rows: DataSourceHealthRow[];
  onRefresh: () => void;
  onToggle: (row: DataSourceHealthRow) => void;
};

export default memo(function DataSourcePanel({ rows, onRefresh, onToggle }: Props) {
  const quoteRows = rows.filter((row) => row.source_type === "quote");
  const klineRows = rows.filter((row) => row.source_type === "kline");
  const enabledCount = rows.filter((row) => row.enabled).length;
  const degradedCount = rows.filter((row) => row.status === "degraded").length;
  const disabledCount = rows.filter((row) => !row.enabled).length;

  return (
    <div className="sources-workspace">
      <section className="panel source-overview-panel">
        <PanelTitle
          title="数据源总览"
          subtitle="关闭后不再参与后续行情刷新，历史健康记录保留"
          action={
            <button className="ghost-btn" onClick={onRefresh}>
              <RefreshCcw size={14} />
              刷新状态
            </button>
          }
        />
        <div className="summary-grid source-summary-grid">
          <MiniStat label="启用源" value={enabledCount} />
          <MiniStat label="降级源" value={degradedCount} />
          <MiniStat label="关闭源" value={disabledCount} />
          <MiniStat label="总记录" value={rows.length} />
        </div>
      </section>

      <SourceHealthPanel title="报价源" subtitle="用于统一报价池合成" rows={quoteRows} onToggle={onToggle} />
      <SourceHealthPanel title="日K源" subtitle="用于补齐本地日 K 缓存" rows={klineRows} onToggle={onToggle} />
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

function SourceHealthPanel({
  title,
  subtitle,
  rows,
  onToggle,
}: {
  title: string;
  subtitle: string;
  rows: DataSourceHealthRow[];
  onToggle: (row: DataSourceHealthRow) => void;
}) {
  return (
    <section className="panel">
      <PanelTitle title={title} subtitle={subtitle} />
      {rows.length ? (
        <div className="source-table">
          {rows.map((row) => (
            <div className={`source-row ${row.enabled ? "" : "disabled"}`} key={`${row.source_type}:${row.source}`}>
              <div className="source-main">
                <div className="source-title">
                  <strong>{row.source}</strong>
                  <Tag text={row.status} />
                  <span className="mini-chip">优先级 {row.priority}</span>
                </div>
                <div className="source-meta">
                  <span>成功 {row.success_count}</span>
                  <span>失败 {row.failure_count}</span>
                  <span>空结果 {row.empty_count}</span>
                  <span>延迟 {row.latency_ms == null ? "-" : `${row.latency_ms.toFixed(0)}ms`}</span>
                </div>
                {row.last_error ? <p>{row.last_error}</p> : null}
              </div>
              <div className="source-status">
                <span>{row.success_rate == null ? "未测试" : `成功率 ${(row.success_rate * 100).toFixed(0)}%`}</span>
                <small>{row.last_success_at ? `最近成功 ${formatShortTime(row.last_success_at)}` : "暂无成功记录"}</small>
              </div>
              <button className={`ghost-btn ${row.enabled ? "subtle" : "primary"}`} onClick={() => onToggle(row)}>
                {row.enabled ? <PauseCircle size={14} /> : <Play size={14} />}
                {row.enabled ? "关闭" : "启用"}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <EmptyHint text="暂无数据源配置" />
      )}
    </section>
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

function displayText(key: string): string {
  const labels: Record<string, string> = {
    healthy: "健康",
    degraded: "降级",
    untested: "未测试",
  };
  return labels[key] ?? businessStatusLabel(key);
}

function displayTagText(text: string) {
  const labels: Record<string, string> = {
    healthy: "健康",
    degraded: "降级",
    untested: "未测试",
  };
  return displayText(text) || labels[text] || businessStatusLabel(text);
}

function toneForTag(text: string) {
  if (["healthy"].includes(text)) return "positive";
  if (["degraded", "untested"].includes(text)) return "warning";
  return "neutral";
}

function formatShortTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1}-${date.getDate()} ${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}
