import { useCallback, useState } from "react";
import {
  Loader2,
  Search,
  BarChart3,
  BrainCircuit,
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronUp,
  Shield,
} from "lucide-react";
import { stockMatrixApi } from "../../api/stockMatrix";
import { scoreColor } from "../../utils/display";
import type {
  StockMatrixAnalysisResult,
  StockMatrixBacktestResult,
  SkillMatrixRow,
  SkillBacktestPerformance,
} from "../../types";

// ── Stance 配置 ──
const STANCE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  strong_support: { label: "强烈看多", color: "#16a34a", bg: "#dcfce7" },
  support: { label: "看多", color: "#16a34a", bg: "#dcfce7" },
  neutral_support: { label: "中性偏多", color: "#65a30d", bg: "#ecfccb" },
  neutral: { label: "中性", color: "#6b7280", bg: "#f3f4f6" },
  oppose: { label: "看空", color: "#dc2626", bg: "#fee2e2" },
  veto: { label: "否决", color: "#991b1b", bg: "#fecaca" },
};

function pct(val: number, digits = 2): string {
  return `${val >= 0 ? "+" : ""}${val.toFixed(digits)}%`;
}

// ── 主页面 ──
export default function StockMatrixPage({ refreshSignal = 0 }: { refreshSignal?: number }) {
  const [code, setCode] = useState("");
  const [tradeDate, setTradeDate] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [includeLlm, setIncludeLlm] = useState(false);
  const [tab, setTab] = useState<"analysis" | "backtest">("analysis");

  const [analysis, setAnalysis] = useState<StockMatrixAnalysisResult | null>(null);
  const [backtest, setBacktest] = useState<StockMatrixBacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runAnalysis = useCallback(async () => {
    if (!code.trim()) return;
    setLoading(true);
    setError("");
    setAnalysis(null);
    try {
      const result = await stockMatrixApi.analysis({
        code: code.trim(),
        trade_date: tradeDate || undefined,
        include_llm: includeLlm,
      });
      setAnalysis(result);
      setTab("analysis");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [code, tradeDate, includeLlm]);

  const runBacktest = useCallback(async () => {
    if (!code.trim() || !startDate || !endDate) return;
    setLoading(true);
    setError("");
    setBacktest(null);
    try {
      const result = await stockMatrixApi.backtest({
        code: code.trim(),
        start_date: startDate,
        end_date: endDate,
      });
      setBacktest(result);
      setTab("backtest");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [code, startDate, endDate]);

  return (
    <div className="stock-matrix-page">
      {/* ── 主搜索栏：玻璃卡片 ── */}
      <section className="sm-searchbar">
        <div className="sm-searchbar-main">
          <label className="sm-field sm-field-code">
            <span>股票代码</span>
            <div className="sm-input-wrap">
              <Search size={15} className="sm-input-icon" />
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && code.trim()) runAnalysis(); }}
                placeholder="000001"
                className="sm-input"
                autoFocus
              />
            </div>
          </label>

          <label className="sm-field">
            <span>分析日期</span>
            <input
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
              placeholder="留空 = 最新交易日"
              className="sm-input sm-input-date"
            />
          </label>

          <button
            onClick={runAnalysis}
            disabled={loading || !code.trim()}
            className="sm-btn sm-btn-primary"
            title="运行矩阵分析（回车也可触发）"
          >
            {loading && tab === "analysis" ? <Loader2 size={15} className="spin" /> : <BarChart3 size={15} />}
            <span>分析</span>
          </button>

          <label className="sm-llm-toggle" title="开启大模型审查（消耗 API 额度）">
            <input type="checkbox" checked={includeLlm} onChange={(e) => setIncludeLlm(e.target.checked)} />
            <BrainCircuit size={14} />
            <span>LLM</span>
          </label>
        </div>

        <div className="sm-searchbar-divider" />

        <div className="sm-searchbar-backtest">
          <label className="sm-field">
            <span>回测起始</span>
            <input
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              placeholder="2026-06-01"
              className="sm-input sm-input-date"
            />
          </label>
          <label className="sm-field">
            <span>回测结束</span>
            <input
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              placeholder="2026-07-01"
              className="sm-input sm-input-date"
            />
          </label>
          <button
            onClick={runBacktest}
            disabled={loading || !code.trim() || !startDate || !endDate}
            className="sm-btn sm-btn-secondary"
          >
            {loading && tab === "backtest" ? <Loader2 size={15} className="spin" /> : <BarChart3 size={15} />}
            <span>回测</span>
          </button>
        </div>
      </section>

      {error && (
        <div className="sm-error">
          <AlertTriangle size={14} />
          <span>{error}</span>
        </div>
      )}

      {/* ── Tab 切换 ── */}
      {(analysis || backtest) && (
        <div className="sm-tabs">
          <button
            onClick={() => setTab("analysis")}
            className={tab === "analysis" ? "active" : ""}
            disabled={!analysis}
          >
            矩阵分析
          </button>
          <button
            onClick={() => setTab("backtest")}
            className={tab === "backtest" ? "active" : ""}
            disabled={!backtest}
          >
            历史回测
          </button>
        </div>
      )}

      {/* ── 分析结果 ── */}
      {tab === "analysis" && analysis && analysis.status === "ok" && (
        <AnalysisResult data={analysis} />
      )}

      {/* ── 回测结果 ── */}
      {tab === "backtest" && backtest && backtest.status === "completed" && (
        <BacktestResult data={backtest} />
      )}
    </div>
  );
}

// ── 分析结果 ──
function AnalysisResult({ data }: { data: StockMatrixAnalysisResult }) {
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const agg = data.aggregated;

  return (
    <div>
      {/* 股票信息 */}
      <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 16 }}>
        <span style={{ fontSize: 18, fontWeight: 700 }}>{data.code}</span>
        <span style={{ fontSize: 16, color: "#6b7280" }}>{data.name}</span>
        <span style={{ fontSize: 16, fontWeight: 600 }}>¥{data.price}</span>
        <span style={{ fontSize: 12, color: "#9ca3af" }}>{data.trade_date}</span>
        <span style={{ fontSize: 12, color: "#9ca3af" }}>{data.elapsed_ms}ms</span>
      </div>

      {/* 聚合评分 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        <div style={{ padding: "12px 20px", background: "#f0f9ff", borderRadius: 8, border: "1px solid #bae6fd" }}>
          <div style={{ fontSize: 11, color: "#0369a1" }}>最终评分</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: scoreColor(agg.final_score) }}>{agg.final_score}</div>
        </div>
        <div style={{ padding: "12px 20px", background: "#faf5ff", borderRadius: 8, border: "1px solid #e9d5ff" }}>
          <div style={{ fontSize: 11, color: "#7c3aed" }}>共识评分</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: "#7c3aed" }}>{agg.consensus_score}</div>
        </div>
        {agg.skill_disagreement && (
          <div style={{ padding: "12px 20px", background: "#fff7ed", borderRadius: 8, border: "1px solid #fed7aa" }}>
            <div style={{ fontSize: 11, color: "#c2410c" }}>Skill 分歧</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: "#c2410c" }}>
              {agg.skill_disagreement.disagreement_level} (σ={agg.skill_disagreement.score_variance?.toFixed(1)})
            </div>
          </div>
        )}
        {agg.risk_flags.length > 0 && (
          <div style={{ padding: "12px 20px", background: "#fef2f2", borderRadius: 8, border: "1px solid #fecaca" }}>
            <div style={{ fontSize: 11, color: "#dc2626" }}>风险标记</div>
            <div style={{ fontSize: 13, color: "#dc2626" }}>{agg.risk_flags.slice(0, 3).join("；")}</div>
          </div>
        )}
      </div>

      {/* Skill 矩阵表 */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, marginBottom: 20 }}>
        <thead>
          <tr style={{ background: "#f9fafb", textAlign: "left" }}>
            <th style={thStyle}>Skill</th>
            <th style={thStyle}>规则分</th>
            <th style={thStyle}>阶段</th>
            <th style={thStyle}>LLM 分</th>
            <th style={thStyle}>立场</th>
            <th style={thStyle}>支撑</th>
            <th style={thStyle}>风险</th>
            <th style={thStyle}>操作</th>
          </tr>
        </thead>
        <tbody>
          {data.skill_results.map((sr) => (
            <SkillRow
              key={sr.skill_id}
              data={sr}
              expanded={expandedSkill === sr.skill_id}
              onToggle={() => setExpandedSkill(expandedSkill === sr.skill_id ? null : sr.skill_id)}
            />
          ))}
        </tbody>
      </table>

      {/* DSA Profile */}
      <div style={{ padding: 16, background: "#f9fafb", borderRadius: 8, fontSize: 13 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>DSA Profile</div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          {Object.entries(data.dsa_profile).map(([k, v]) => (
            <span key={k}>
              <span style={{ color: "#6b7280" }}>{k}: </span>
              <span style={{ fontWeight: 500 }}>{v != null ? String(v) : "-"}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── 单个 Skill 行 ──
function SkillRow({ data, expanded, onToggle }: { data: SkillMatrixRow; expanded: boolean; onToggle: () => void }) {
  const llm = data.llm_review;
  const stanceCfg = llm?.stance ? STANCE_CONFIG[llm.stance] || STANCE_CONFIG.neutral : null;

  return (
    <>
      <tr style={{ borderBottom: "1px solid #e5e7eb" }}>
        <td style={tdStyle}>
          <span style={{ fontWeight: 500 }}>{data.skill_name}</span>
          {data.hard_veto && (
            <span style={{ marginLeft: 6, fontSize: 11, color: "#dc2626", background: "#fee2e2", padding: "1px 6px", borderRadius: 4 }}>
              VETO
            </span>
          )}
        </td>
        <td style={tdStyle}>
          <span style={{ fontWeight: 600, color: scoreColor(data.rule_score) }}>{data.rule_score}</span>
        </td>
        <td style={tdStyle}>
          <span style={{ fontSize: 12, color: data.rule_phase.includes("回避") || data.rule_phase.includes("退潮") ? "#dc2626" : "#374151" }}>
            {data.rule_phase}
          </span>
        </td>
        <td style={tdStyle}>
          {llm && llm.score != null ? (
            <span style={{ fontWeight: 600, color: scoreColor(llm.score) }}>{llm.score}</span>
          ) : (
            <span style={{ color: "#d1d5db" }}>-</span>
          )}
        </td>
        <td style={tdStyle}>
          {stanceCfg ? (
            <span style={{ padding: "2px 8px", borderRadius: 4, fontSize: 11, background: stanceCfg.bg, color: stanceCfg.color, fontWeight: 500 }}>
              {stanceCfg.label}
            </span>
          ) : (
            <span style={{ color: "#d1d5db" }}>-</span>
          )}
        </td>
        <td style={tdStyle}>
          {llm?.supports?.length ?? data.rule_evidence.length}
        </td>
        <td style={tdStyle}>
          {llm?.risks?.length ?? data.rule_risk_flags.length}
        </td>
        <td style={tdStyle}>
          <button onClick={onToggle} style={{ background: "none", border: "none", cursor: "pointer", padding: 4 }}>
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} style={{ padding: 0 }}>
            <div style={{ padding: "12px 16px", background: "#fafafa", borderBottom: "2px solid #e5e7eb" }}>
              {/* 规则证据 */}
              {data.rule_evidence.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 11, color: "#6b7280", marginBottom: 4, fontWeight: 600 }}>规则证据</div>
                  {data.rule_evidence.map((e, i) => (
                    <div key={i} style={{ fontSize: 12, color: "#374151", paddingLeft: 8, borderLeft: "2px solid #d1d5db", marginBottom: 2 }}>
                      {e}
                    </div>
                  ))}
                </div>
              )}
              {/* 规则风险 */}
              {data.rule_risk_flags.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 11, color: "#dc2626", marginBottom: 4, fontWeight: 600, display: "flex", alignItems: "center", gap: 4 }}>
                    <AlertTriangle size={12} /> 规则风险
                  </div>
                  {data.rule_risk_flags.map((r, i) => (
                    <div key={i} style={{ fontSize: 12, color: "#dc2626", paddingLeft: 8, borderLeft: "2px solid #fecaca", marginBottom: 2 }}>
                      {r}
                    </div>
                  ))}
                </div>
              )}
              {/* LLM 审查详情 */}
              {llm && llm.status !== "error" && (
                <div>
                  <div style={{ fontSize: 11, color: "#7c3aed", marginBottom: 4, fontWeight: 600, display: "flex", alignItems: "center", gap: 4 }}>
                    <BrainCircuit size={12} /> LLM 审查
                  </div>
                  {llm.summary && <div style={{ fontSize: 12, marginBottom: 4, fontWeight: 500 }}>{llm.summary}</div>}
                  {llm.supports && llm.supports.length > 0 && (
                    <div style={{ marginBottom: 4 }}>
                      {llm.supports.map((s, i) => (
                        <div key={i} style={{ fontSize: 12, color: "#16a34a", paddingLeft: 8, borderLeft: "2px solid #bbf7d0", marginBottom: 2, display: "flex", alignItems: "flex-start", gap: 4 }}>
                          <CheckCircle2 size={11} style={{ marginTop: 2, flexShrink: 0 }} /> {s}
                        </div>
                      ))}
                    </div>
                  )}
                  {llm.risks && llm.risks.length > 0 && (
                    <div>
                      {llm.risks.map((r, i) => (
                        <div key={i} style={{ fontSize: 12, color: "#dc2626", paddingLeft: 8, borderLeft: "2px solid #fecaca", marginBottom: 2, display: "flex", alignItems: "flex-start", gap: 4 }}>
                          <AlertTriangle size={11} style={{ marginTop: 2, flexShrink: 0 }} /> {r}
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 4 }}>
                    行动: {llm.action} | 仓位: {llm.position_range} | 置信度: {(llm.confidence * 100).toFixed(0)}%
                  </div>
                  {llm.hallucination_check?.severity === "high" && (
                    <div style={{ fontSize: 11, color: "#dc2626", marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
                      <Shield size={11} /> 幻觉检测: {llm.hallucination_check.severity}
                    </div>
                  )}
                </div>
              )}
              {llm?.status === "error" && (
                <div style={{ fontSize: 12, color: "#dc2626" }}>LLM 错误: {llm.error}</div>
              )}
              {/* 入场规则 */}
              {(data.entry_rule || data.invalid_rule) && (
                <div style={{ marginTop: 8, padding: 8, background: "#f0f9ff", borderRadius: 4, fontSize: 12 }}>
                  {data.entry_rule && <div><span style={{ color: "#0369a1" }}>入场: </span>{data.entry_rule}</div>}
                  {data.invalid_rule && <div><span style={{ color: "#dc2626" }}>失效: </span>{data.invalid_rule}</div>}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── 回测结果 ──
function BacktestResult({ data }: { data: StockMatrixBacktestResult }) {
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const agg = data.aggregate_metrics;

  return (
    <div>
      {/* 股票信息 */}
      <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 16 }}>
        <span style={{ fontSize: 18, fontWeight: 700 }}>{data.code}</span>
        <span style={{ fontSize: 16, color: "#6b7280" }}>{data.name}</span>
        <span style={{ fontSize: 12, color: "#9ca3af" }}>
          {data.start_date} ~ {data.end_date} ({data.dates_scanned} 天)
        </span>
      </div>

      {/* 聚合指标 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        <MetricCard label="总信号" value={String(agg.total_signals)} />
        <MetricCard label="总交易" value={String(agg.total_trades)} />
        <MetricCard label="总体胜率" value={pct(agg.overall_win_rate * 100, 1)} highlight={agg.overall_win_rate >= 0.5} />
        <MetricCard label="平均收益" value={pct(agg.overall_avg_return)} highlight={agg.overall_avg_return > 0} />
        <MetricCard label="最佳 Skill" value={agg.best_skill_id.split("-").slice(0, 2).join("-")} />
        <MetricCard label="最差 Skill" value={agg.worst_skill_id.split("-").slice(0, 2).join("-")} />
      </div>

      {/* Per-skill 表 */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, marginBottom: 20 }}>
        <thead>
          <tr style={{ background: "#f9fafb", textAlign: "left" }}>
            <th style={thStyle}>Skill</th>
            <th style={thStyle}>信号</th>
            <th style={thStyle}>交易</th>
            <th style={thStyle}>胜率</th>
            <th style={thStyle}>平均收益</th>
            <th style={thStyle}>盈亏比</th>
            <th style={thStyle}>最大回撤</th>
            <th style={thStyle}>操作</th>
          </tr>
        </thead>
        <tbody>
          {data.per_skill_performance.map((perf) => (
            <BacktestSkillRow
              key={perf.skill_id}
              data={perf}
              expanded={expandedSkill === perf.skill_id}
              onToggle={() => setExpandedSkill(expandedSkill === perf.skill_id ? null : perf.skill_id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BacktestSkillRow({ data, expanded, onToggle }: { data: SkillBacktestPerformance; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr style={{ borderBottom: "1px solid #e5e7eb" }}>
        <td style={tdStyle}>{data.skill_name}</td>
        <td style={tdStyle}>{data.signal_count}</td>
        <td style={tdStyle}>{data.triggered_count}</td>
        <td style={tdStyle}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ width: 60, height: 6, background: "#e5e7eb", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${data.win_rate * 100}%`, height: "100%", background: data.win_rate >= 0.5 ? "#16a34a" : "#dc2626", borderRadius: 3 }} />
            </div>
            <span style={{ color: data.win_rate >= 0.5 ? "#16a34a" : "#dc2626", fontWeight: 600 }}>
              {(data.win_rate * 100).toFixed(1)}%
            </span>
          </div>
        </td>
        <td style={tdStyle}>
          <span style={{ color: data.avg_return >= 0 ? "#16a34a" : "#dc2626", fontWeight: 600 }}>
            {pct(data.avg_return)}
          </span>
        </td>
        <td style={tdStyle}>{data.profit_factor.toFixed(2)}</td>
        <td style={tdStyle}>
          <span style={{ color: "#dc2626" }}>{pct(data.max_drawdown)}</span>
        </td>
        <td style={tdStyle}>
          {data.trades.length > 0 && (
            <button onClick={onToggle} style={{ background: "none", border: "none", cursor: "pointer", padding: 4 }}>
              {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
          )}
        </td>
      </tr>
      {expanded && data.trades.length > 0 && (
        <tr>
          <td colSpan={8} style={{ padding: 0 }}>
            <div style={{ padding: "8px 16px", background: "#fafafa", borderBottom: "2px solid #e5e7eb" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ background: "#f3f4f6" }}>
                    <th style={smallThStyle}>交易日</th>
                    <th style={smallThStyle}>入场日</th>
                    <th style={smallThStyle}>入场价</th>
                    <th style={smallThStyle}>出场日</th>
                    <th style={smallThStyle}>出场价</th>
                    <th style={smallThStyle}>净收益</th>
                    <th style={smallThStyle}>最大收益</th>
                    <th style={smallThStyle}>最大回撤</th>
                    <th style={smallThStyle}>出场原因</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trades.map((t, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #e5e7eb" }}>
                      <td style={smallTdStyle}>{t.trade_date}</td>
                      <td style={smallTdStyle}>{t.entry_date}</td>
                      <td style={smallTdStyle}>¥{t.entry_price}</td>
                      <td style={smallTdStyle}>{t.exit_date}</td>
                      <td style={smallTdStyle}>¥{t.exit_price}</td>
                      <td style={{ ...smallTdStyle, color: t.net_return >= 0 ? "#16a34a" : "#dc2626", fontWeight: 600 }}>
                        {pct(t.net_return)}
                      </td>
                      <td style={{ ...smallTdStyle, color: "#16a34a" }}>{pct(t.max_return)}</td>
                      <td style={{ ...smallTdStyle, color: "#dc2626" }}>{pct(t.max_drawdown)}</td>
                      <td style={smallTdStyle}>{t.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── 小组件 ──
function MetricCard({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div style={{ padding: "10px 16px", background: highlight ? "#f0fdf4" : "#f9fafb", borderRadius: 8, border: `1px solid ${highlight ? "#bbf7d0" : "#e5e7eb"}` }}>
      <div style={{ fontSize: 11, color: "#6b7280" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: highlight ? "#16a34a" : "#111827" }}>{value}</div>
    </div>
  );
}

const thStyle: React.CSSProperties = { padding: "8px 10px", borderBottom: "2px solid #e5e7eb", fontSize: 12, fontWeight: 600, color: "#374151" };
const tdStyle: React.CSSProperties = { padding: "8px 10px", borderBottom: "1px solid #f3f4f6" };
const smallThStyle: React.CSSProperties = { padding: "4px 8px", borderBottom: "1px solid #e5e7eb", fontSize: 11, fontWeight: 500, color: "#6b7280", textAlign: "left" };
const smallTdStyle: React.CSSProperties = { padding: "4px 8px", fontSize: 11 };
