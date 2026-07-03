import { useEffect, useState } from "react";
import { BarChart3, Play, ShieldAlert, RefreshCw } from "lucide-react";
import { api } from "../../../api/client";

export default function TemporalConsistencyPanel() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchTemporal = async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      setData(await api.temporalConsistency("formal", { signal }));
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error ? err.message : "时态一致性检查失败");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    fetchTemporal(controller.signal);
    return () => controller.abort();
  }, []);

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
        <button className="btn" onClick={() => void fetchTemporal()} disabled={loading}>
          <RefreshCw size={14} />
          检查
        </button>
        {loading && <span style={{ color: "var(--muted)", fontSize: 12 }}>检查中...</span>}
      </div>

      {error && <div className="bt-error" style={{ marginTop: 12 }}>{error}</div>}

      {data && !loading && (
        <>
          {status === "no_data" ? (
            <div className="empty-state actionable" style={{ marginTop: 12 }}>
              <strong>暂无推荐数据可检查</strong>
              <span>时态一致性需要基于推荐样本检查，先运行推荐或查看候选是否已产出。</span>
              <div className="state-actions">
                <button type="button" onClick={() => navigateToSection("run")}>
                  <Play size={14} />
                  运行推荐
                </button>
                <button type="button" onClick={() => navigateToSection("pool")}>
                  <BarChart3 size={14} />
                  查看候选
                </button>
              </div>
            </div>
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
                    {violations.slice(0, 30).map((v, i) => {
                      const severity = String(v.severity || "");
                      const check = String(v.check || "");
                      const message = String(v.message || "");
                      const code = v.code ? String(v.code) : "";
                      const tradeDate = v.trade_date ? String(v.trade_date) : "";
                      return (
                        <div
                          key={i}
                          className="fp-item"
                          style={{
                            borderColor: severity === "error"
                              ? "color-mix(in srgb, var(--danger) 42%, var(--line))"
                              : undefined
                          }}
                        >
                          <strong style={{
                            color: severity === "error" ? "var(--danger)" : "var(--warning)"
                          }}>
                            {severity === "error" ? "🔴" : "🟡"} {check}
                          </strong>
                          <span>{message}</span>
                          {code && <span>股票: {code}</span>}
                          {tradeDate && <span>日期: {tradeDate}</span>}
                        </div>
                      );
                    })}
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

function navigateToSection(section: "run" | "pool") {
  window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section } }));
}

function isAbortError(err: unknown) {
  return err instanceof Error && err.name === "AbortError";
}
