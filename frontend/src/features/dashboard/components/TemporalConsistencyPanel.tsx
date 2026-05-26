import { useEffect, useState } from "react";
import { ShieldAlert, RefreshCw } from "lucide-react";
import { api } from "../../../api/client";

export default function TemporalConsistencyPanel() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchTemporal = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.temporalConsistency("formal"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "时态一致性检查失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchTemporal(); }, []);

  const summary = data?.check_summary as Record<string, number> | undefined;
  const violations = data?.violations as Array<Record<string, unknown>> | undefined;
  const totalViolations = summary?.total ?? 0;
  const errors = summary?.failed ?? 0;
  const warnings = summary?.warnings ?? 0;
  const status = data?.status as string;

  const hasIssues = totalViolations > 0;

  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Validation</div>
          <h2>时态一致性检查</h2>
        </div>
        <ShieldAlert size={19} />
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
        <button className="btn" onClick={fetchTemporal} disabled={loading}>
          <RefreshCw size={14} />
          检查
        </button>
        {loading && <span style={{ color: "var(--muted)", fontSize: 12 }}>检查中...</span>}
      </div>

      {error && <div className="bt-error" style={{ marginTop: 12 }}>{error}</div>}

      {data && !loading && (
        <>
          {status === "no_data" ? (
            <div className="bt-error" style={{ marginTop: 12 }}>暂无推荐数据可检查</div>
          ) : (
            <>
              {/* 汇总 */}
              <div className="risk-grid compact-risk" style={{ marginTop: 12 }}>
                <span>
                  <strong className={errors > 0 ? "negative" : "positive"}>{errors}</strong>
                  错误
                </span>
                <span>
                  <strong className={warnings > 0 ? undefined : "positive"}>{warnings}</strong>
                  警告
                </span>
                <span>
                  <strong>{totalViolations}</strong>
                  违规总数
                </span>
              </div>

              <div className="bt-summary-grid" style={{ marginTop: 8, gridTemplateColumns: "repeat(2, 1fr)" }}>
                <div className="mini-stat">
                  <span>检查次数</span>
                  <strong>{summary?.total ?? 0}</strong>
                </div>
                <div className="mini-stat">
                  <span>通过</span>
                  <strong>{summary?.passed ?? 0}</strong>
                </div>
              </div>

              {/* 违规列表 */}
              {hasIssues && violations && (
                <details className="fp-details" style={{ marginTop: 12 }}>
                  <summary>
                    违规详情 ({violations.length} 条)
                    <small>点击展开</small>
                  </summary>
                  <div className="fp-list">
                    {violations.slice(0, 30).map((v, i) => (
                      <div
                        key={i}
                        className="fp-item"
                        style={{
                          borderColor: v.severity === "error"
                            ? "color-mix(in srgb, var(--danger) 42%, var(--line))"
                            : undefined
                        }}
                      >
                        <strong style={{
                          color: v.severity === "error" ? "var(--danger)" : "var(--warning)"
                        }}>
                          {v.severity === "error" ? "🔴" : "🟡"} {v.check as string}
                        </strong>
                        <span>{v.message as string}</span>
                        {v.code && <span>股票: {v.code as string}</span>}
                        {v.trade_date && <span>日期: {v.trade_date as string}</span>}
                      </div>
                    ))}
                  </div>
                </details>
              )}

              {!hasIssues && (
                <div className="control-banner positive" style={{ marginTop: 12 }}>
                  <strong>✓ 未发现时态违规</strong>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
