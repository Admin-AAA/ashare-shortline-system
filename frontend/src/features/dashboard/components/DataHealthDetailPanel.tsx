import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { api } from "../../../api/client";

function textValue(value: unknown, fallback = "0") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

export default function DataHealthDetailPanel() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchHealth = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.dataHealth());
    } catch (err) {
      setError(err instanceof Error ? err.message : "数据健康诊断失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchHealth(); }, []);

  const score = data?.score as number | undefined;
  const tables = data?.tables as Record<string, unknown> | undefined;
  const issues = data?.issues as Array<{ severity: string; message: string }> | undefined;
  const lagDays = data?.lag_days as Record<string, number> | undefined;
  const recSummary = data?.recommendation_summary as Record<string, unknown> | undefined;

  const criticalTables = ["daily_kbar", "recommendation", "limit_up_board"];
  const healthLevel = score !== undefined ? (score >= 80 ? "good" : score >= 50 ? "warning" : "bad") : "unknown";

  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Diagnostics</div>
          <h2>数据质量诊断</h2>
        </div>
        <Activity size={19} />
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
        <button className="btn" onClick={fetchHealth} disabled={loading}>
          <RefreshCw size={14} />
          刷新
        </button>
        {loading && <span style={{ color: "var(--muted)", fontSize: 12 }}>诊断中...</span>}
      </div>

      {error && <div className="bt-error" style={{ marginTop: 12 }}>{error}</div>}

      {data && !loading && (
        <>
          {/* 健康评分 */}
          <div
            className="control-banner"
            style={{
              marginTop: 12,
              borderColor:
                healthLevel === "good"
                  ? "color-mix(in srgb, var(--positive) 42%, var(--line))"
                  : healthLevel === "warning"
                    ? "color-mix(in srgb, var(--warning) 42%, var(--line))"
                    : "color-mix(in srgb, var(--danger) 42%, var(--line))",
              color: healthLevel === "good" ? "var(--positive)" : healthLevel === "warning" ? "var(--warning)" : "var(--danger)",
            }}
          >
            <strong>健康评分: {score ?? "N/A"}/100</strong>
            <div style={{ fontSize: 12, marginTop: 4, color: "var(--muted)" }}>
              Tushare 最新交易日: {textValue(data.tushare_latest_trade_date, "未知")}
            </div>
          </div>

          {/* 关键表覆盖 */}
          <div className="kpi-grid" style={{ marginTop: 12 }}>
            {tables && criticalTables.map((key) => {
              const tbl = tables[key] as Record<string, unknown> | undefined;
              if (!tbl) return null;
              const isHealthy = (tbl.total_rows as number) > 0;
              return (
                <div
                  key={key}
                  className={`kpi-card ${isHealthy ? "positive" : "negative"}`}
                  style={{ borderColor: isHealthy ? "color-mix(in srgb, var(--positive) 32%, var(--line))" : undefined }}
                >
                  <span className="kpi-value" style={{ fontSize: 14 }}>{textValue(tbl.label, key)}</span>
                  <span className="kpi-label">
                    {tbl.total_rows as number > 0
                      ? `${textValue(tbl.total_rows)} 条 · ${textValue(tbl.max_date, "-")}`
                      : "空表"}
                  </span>
                  {lagDays && lagDays[key] !== undefined && (
                    <span className="kpi-label">
                      滞后 <strong>{lagDays[key]}天</strong>
                    </span>
                  )}
                  {tbl.error && <span className="kpi-label">错误: {textValue(tbl.error)}</span>}
                </div>
              );
            })}
          </div>

          {/* 所有表的完整诊断 */}
          {tables && (
            <details className="fp-details" style={{ marginTop: 12 }}>
              <summary>所有表一览 <small>点击展开</small></summary>
              <div className="fp-list">
                {Object.entries(tables).map(([key, tbl]) => {
                  const t = tbl as Record<string, unknown>;
                  return (
                    <div key={key} className="fp-item">
                      <strong>{textValue(t.label, key)}</strong>
                      <span>
                        {textValue(t.total_rows)} 行
                        {t.min_date ? ` · ${t.min_date} ~ ${t.max_date}` : ""}
                        {t.error ? ` · 错误: ${t.error}` : ""}
                      </span>
                      {lagDays && lagDays[key] !== undefined && (
                        <span>滞后: {lagDays[key]} 天</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>
          )}

          {/* 问题列表 */}
          {issues && issues.length > 0 && (
            <details className="fp-details" style={{ marginTop: 12 }}>
              <summary>发现 {issues.length} 个问题 <small>点击展开</small></summary>
              <div className="fp-list">
                {issues.map((issue, i) => (
                  <div key={i} className="fp-item" style={{
                    borderColor: issue.severity === "high"
                      ? "color-mix(in srgb, var(--danger) 42%, var(--line))"
                      : undefined
                  }}>
                    <strong style={{
                      color: issue.severity === "high" ? "var(--danger)"
                        : issue.severity === "medium" ? "var(--warning)"
                        : undefined
                    }}>
                      {issue.severity === "high" ? "🔴" : issue.severity === "medium" ? "🟡" : "🟢"} {issue.message}
                    </strong>
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* 推荐摘要 */}
          {recSummary && (
            <div className="sub-metrics" style={{ marginTop: 12 }}>
              <div className="sub-metrics-group">
                <span className="group-label">推荐摘要</span>
                <div className="sub-metrics-row">
                  <span>总数 <strong>{textValue(recSummary.total)}</strong></span>
                  <span>已标注 <strong>{textValue(recSummary.labeled_count)}</strong></span>
                  <span>最新日期 <strong>{textValue(recSummary.latest_trade_date, "-")}</strong></span>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
