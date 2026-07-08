import { useEffect, useState } from "react";
import { ArrowLeft, Loader2, PlayCircle, Search, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { Preset, RunDetail, SingleStockAnalysis, SingleStockSkillReview } from "../types";
import { ErrorPage } from "../components/StateViews";
import { CollapsiblePanel } from "../components/CollapsiblePanel";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";

/**
 * 运行详情页 —— 从驾驶舱主 CTA 进入
 * 展示阶段时间线、进度、产出摘要。完成后自动引导到候选池。
 */
export function RunDetailPage({ refreshSignal }: { refreshSignal: number }) {
  const { navigate, navigateSection } = useUrlRoute();
  const runId = queryParam("run_id");

  const [presets, setPresets] = useState<Preset[]>([]);
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.presets().then((p) => {
      setPresets(p);
      if (p.length && !selectedPreset) setSelectedPreset(p[0].preset_id);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!runId) return;
    api.runDetail(runId).then(setDetail).catch((e) => setError(e.message || "运行详情加载失败"));
  }, [runId, refreshSignal]);

  async function startRun() {
    if (!selectedPreset) return;
    setRunning(true);
    setMessage("正在触发推荐运行…");
    try {
      const result = await api.createRun({
        preset_id: selectedPreset,
        run_name: `驾驶舱 · 手动推荐`,
      });
      setMessage(`推荐已触发：${result.run.run_id}`);
      navigate("/run", { params: { run_id: result.run.run_id } });
    } catch (e) {
      setMessage(`运行失败：${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <button className="btn btn--ghost" onClick={() => navigateSection("dashboard")} style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={14} />
        返回驾驶舱
      </button>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: "var(--space-4)", alignItems: "start" }}>
        <CollapsiblePanel
          collapsedKey="run-orchestration"
          kicker="推荐运行"
          title={
            <>
              <PlayCircle size={18} />
              运行编排
            </>
          }
          bodyStyle={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
        >
          <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
            <label className="text-muted" style={{ fontSize: "var(--text-sm)" }}>运行预设</label>
            <select
              value={selectedPreset}
              onChange={(e) => setSelectedPreset(e.target.value)}
              style={{
                background: "var(--surface-soft)",
                border: "1px solid var(--line-strong)",
                borderRadius: "var(--radius-md)",
                padding: "var(--space-2) var(--space-3)",
                color: "var(--ink)",
                fontFamily: "inherit",
              }}
            >
              {presets.map((p) => (
                <option key={p.preset_id} value={p.preset_id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          <button
            className="btn btn--primary"
            onClick={startRun}
            disabled={running || !selectedPreset}
            style={{ padding: "var(--space-3) var(--space-5)" }}
          >
            {running ? <Loader2 size={16} className="spin" /> : <PlayCircle size={16} />}
            {running ? "提交中…" : "立即运行"}
          </button>

          {message && (
            <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>
              {message}
            </div>
          )}
        </CollapsiblePanel>

        <SingleStockScorePanel presets={presets} />
      </div>

      {error && <ErrorPage title="运行详情加载失败" message={error} onRetry={() => window.location.reload()} />}

      {detail && <RunTimeline detail={detail} />}
    </div>
  );
}

// ============================================================
// 单股票评分面板
// ============================================================

function SingleStockScorePanel({ presets }: { presets: Preset[] }) {
  const [code, setCode] = useState("");
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [includeLlm, setIncludeLlm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SingleStockAnalysis | null>(null);
  const [error, setError] = useState("");

  async function runScore() {
    const trimmed = code.trim();
    if (!trimmed) {
      setError("请输入股票代码");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.analyzeStock({
        code: trimmed,
        include_llm: includeLlm,
        preset_id: selectedPreset || undefined,
      });
      if (res.status === "error") {
        setError(res.message || "分析失败");
      } else {
        setResult(res);
      }
    } catch (e) {
      setError((e as Error).message || "单股票评分失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <CollapsiblePanel
      collapsedKey="single-stock-score"
      kicker="单股票评分"
      title={
        <>
          <Search size={18} />
          单股票评分
        </>
      }
      bodyStyle={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
    >
      <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>
        输入股票代码，对单只股票运行基础短线策略特征构建 + 评分流程。可选选择预设并触发 LLM 多 Skill 聚合评分（与推荐管线一致，消耗 API 额度）。
      </div>

      <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center", flexWrap: "wrap" }}>
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") runScore(); }}
          placeholder="股票代码，如 000001"
          style={{
            background: "var(--surface-soft)",
            border: "1px solid var(--line-strong)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-2) var(--space-3)",
            color: "var(--ink)",
            fontFamily: "var(--font-mono)",
            width: 200,
          }}
        />
        <select
          value={selectedPreset}
          onChange={(e) => setSelectedPreset(e.target.value)}
          style={{
            background: "var(--surface-soft)",
            border: "1px solid var(--line-strong)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-2) var(--space-3)",
            color: "var(--ink)",
            fontFamily: "inherit",
          }}
        >
          <option value="">不选预设（单 skill）</option>
          {presets.map((p) => (
            <option key={p.preset_id} value={p.preset_id}>{p.name}</option>
          ))}
        </select>
        <label style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-sm)", color: "var(--ink-secondary)", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={includeLlm}
            onChange={(e) => setIncludeLlm(e.target.checked)}
            style={{ cursor: "pointer" }}
          />
          <Sparkles size={14} />
          触发 LLM 深度分析
        </label>
        <button
          className="btn btn--primary"
          onClick={runScore}
          disabled={loading || !code.trim()}
          style={{ padding: "var(--space-2) var(--space-4)" }}
        >
          {loading ? <Loader2 size={16} className="spin" /> : <Search size={16} />}
          {loading ? "评分中…" : "开始评分"}
        </button>
      </div>

      {error && (
        <div style={{ color: "var(--market-down)", fontSize: "var(--text-sm)" }}>{error}</div>
      )}

      {result && <SingleStockScoreResult result={result} />}
    </CollapsiblePanel>
  );
}

function ScoreMetric({ label, value, suffix }: { label: string; value: number | null | undefined; suffix?: string }) {
  const display = value == null ? "—" : value.toFixed(1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>{label}</span>
      <span className="text-mono" style={{ fontSize: "var(--text-base)", fontWeight: 600 }}>
        {display}{suffix}
      </span>
    </div>
  );
}

function SingleStockScoreResult({ result }: { result: SingleStockAnalysis }) {
  const s = result.scoring;
  const p = result.dsa_profile;
  const e = result.entry_rules;
  const llm = result.llm_analysis;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)", marginTop: "var(--space-2)" }}>
      {/* 股票基本信息 */}
      <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
          <div>
            <span className="text-mono" style={{ fontSize: "var(--text-lg)", fontWeight: 700 }}>{result.code}</span>
            <span style={{ marginLeft: "var(--space-3)", color: "var(--ink)" }}>{result.name}</span>
          </div>
          <span className="tag">{result.trade_date}</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "var(--space-3)" }}>
          <ScoreMetric label="最新价" value={result.price} />
          <ScoreMetric label="K线条数" value={result.bar_count} />
          <ScoreMetric label="耗时(ms)" value={result.elapsed_ms} />
        </div>
      </section>

      {/* 基础短线策略评分 */}
      <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>基础短线策略评分</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: "var(--space-3)" }}>
          <ScoreMetric label="信号分" value={s.dsa_signal_score} />
          <ScoreMetric label="趋势分" value={s.dsa_trend_score} />
          <ScoreMetric label="乖离分" value={s.dsa_bias_score} />
          <ScoreMetric label="量能分" value={s.dsa_volume_score} />
          <ScoreMetric label="支撑分" value={s.dsa_support_score} />
          <ScoreMetric label="MACD分" value={s.dsa_macd_score} />
          <ScoreMetric label="RSI分" value={s.dsa_rsi_score} />
          <ScoreMetric label="风险分" value={s.risk_score} />
        </div>
      </section>

      {/* 基础短线策略特征 */}
      <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>基础短线策略特征</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: "var(--space-3)" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>趋势状态</span>
            <span style={{ fontSize: "var(--text-sm)" }}>{p.trend_status || "—"}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>量能状态</span>
            <span style={{ fontSize: "var(--text-sm)" }}>{p.volume_status || "—"}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>MACD状态</span>
            <span style={{ fontSize: "var(--text-sm)" }}>{p.macd_status || "—"}</span>
          </div>
          <ScoreMetric label="MACD_DIF" value={p.macd_dif} />
          <ScoreMetric label="MACD_DEA" value={p.macd_dea} />
          <ScoreMetric label="RSI_12" value={p.rsi_12} />
          <ScoreMetric label="乖离MA5" value={p.bias_ma5} suffix="%" />
          <ScoreMetric label="乖离MA10" value={p.bias_ma10} suffix="%" />
        </div>
      </section>

      {/* 买卖点规则 */}
      <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>买卖点规则</div>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          <div>
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>理想买点：</span>
            <span className="text-mono" style={{ fontSize: "var(--text-sm)" }}>{e.ideal_buy != null ? e.ideal_buy.toFixed(2) : "—"}</span>
          </div>
          <div>
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>次级买点：</span>
            <span className="text-mono" style={{ fontSize: "var(--text-sm)" }}>{e.secondary_buy != null ? e.secondary_buy.toFixed(2) : "—"}</span>
          </div>
          <div style={{ display: "flex", gap: "var(--space-4)" }}>
            <div>
              <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>止损价：</span>
              <span className="text-mono" style={{ fontSize: "var(--text-sm)" }}>{e.stop_loss != null ? e.stop_loss.toFixed(2) : "—"}</span>
            </div>
            <div>
              <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>止盈价：</span>
              <span className="text-mono" style={{ fontSize: "var(--text-sm)" }}>{e.take_profit != null ? e.take_profit.toFixed(2) : "—"}</span>
            </div>
          </div>
        </div>
      </section>

      {/* LLM 分析结果 */}
      {llm && <SingleStockLlmSection llm={llm} />}
    </div>
  );
}

function SingleStockLlmSection({ llm }: { llm: NonNullable<SingleStockAnalysis["llm_analysis"]> }) {
  if (llm.status === "error") {
    return (
      <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>LLM 深度分析</div>
        <div style={{ color: "var(--market-down)", fontSize: "var(--text-sm)" }}>分析失败：{llm.error || "未知错误"}</div>
      </section>
    );
  }

  // 预设多 skill 聚合评分模式
  if (llm.final_score != null && llm.skill_reviews && llm.skill_reviews.length > 0) {
    return <PresetLlmSection llm={llm} />;
  }

  const stanceColor =
    llm.stance === "support" ? "var(--market-up)" :
    llm.stance === "oppose" ? "var(--market-down)" :
    "var(--muted)";

  return (
    <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
        <div className="panel__kicker">LLM 深度分析</div>
        {llm.elapsed_ms != null && (
          <span className="text-muted text-mono" style={{ fontSize: "var(--text-xs)" }}>{llm.elapsed_ms}ms</span>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "var(--space-3)", marginBottom: "var(--space-3)" }}>
        <ScoreMetric label="LLM评分" value={llm.score} />
        <ScoreMetric label="规则基线" value={llm.rule_score} />
        <ScoreMetric label="LLM调整" value={llm.llm_adjustment} />
        <ScoreMetric label="置信度" value={llm.confidence != null ? llm.confidence * 100 : null} suffix="%" />
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>立场</span>
          <span style={{ fontSize: "var(--text-sm)", color: stanceColor, fontWeight: 600 }}>{llm.stance || "—"}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>建议操作</span>
          <span style={{ fontSize: "var(--text-sm)" }}>{llm.action || "—"}</span>
        </div>
      </div>

      {llm.summary && (
        <div style={{ marginBottom: "var(--space-2)", fontSize: "var(--text-sm)", color: "var(--ink-secondary)" }}>
          {llm.summary}
        </div>
      )}

      {llm.supports && llm.supports.length > 0 && (
        <div style={{ marginBottom: "var(--space-2)" }}>
          <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>支持理由：</span>
          <ul style={{ margin: "var(--space-1) 0 0 var(--space-4)", padding: 0, fontSize: "var(--text-sm)" }}>
            {llm.supports.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {llm.risks && llm.risks.length > 0 && (
        <div style={{ marginBottom: "var(--space-2)" }}>
          <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>风险提示：</span>
          <ul style={{ margin: "var(--space-1) 0 0 var(--space-4)", padding: 0, fontSize: "var(--text-sm)", color: "var(--market-down)" }}>
            {llm.risks.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}

      {(llm.total_tokens_real || llm.total_tokens_est) && (
        <div className="text-muted text-mono" style={{ fontSize: "var(--text-xs)", borderTop: "1px solid var(--border)", paddingTop: "var(--space-2)" }}>
          Token 用量：{llm.total_tokens_real || llm.total_tokens_est}
          {llm.total_cache_hit_tokens ? ` · 缓存命中 ${llm.total_cache_hit_tokens}` : ""}
        </div>
      )}
    </section>
  );
}

// ============================================================
// 预设多 skill 聚合评分展示
// ============================================================

function PresetLlmSection({ llm }: { llm: NonNullable<SingleStockAnalysis["llm_analysis"]> }) {
  const cs = llm.component_scores || {};
  const reviews = llm.skill_reviews || [];
  const validReviews = reviews.filter((r) => r.status === "ok");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      {/* 聚合评分总览 */}
      <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
          <div className="panel__kicker">预设聚合评分 · {llm.preset_name || llm.preset_id}</div>
          {llm.elapsed_ms != null && (
            <span className="text-muted text-mono" style={{ fontSize: "var(--text-xs)" }}>{llm.elapsed_ms}ms</span>
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "var(--space-3)" }}>
          <ScoreMetric label="最终评分" value={llm.final_score} />
          <ScoreMetric label="Agent审查分" value={llm.agent_review_score} />
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>风险模板</span>
            <span style={{ fontSize: "var(--text-sm)" }}>{llm.risk_template || "—"}</span>
          </div>
          <ScoreMetric label="有效 Skill 数" value={validReviews.length} />
          <ScoreMetric label="总 Skill 数" value={reviews.length} />
        </div>
      </section>

      {/* 组件评分细分 */}
      {Object.keys(cs).length > 0 && (
        <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
          <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>组件评分</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "var(--space-3)" }}>
            {Object.entries(cs).map(([key, val]) => (
              <ScoreMetric key={key} label={key} value={typeof val === "number" ? val : null} />
            ))}
          </div>
        </section>
      )}

      {/* 各 Skill 审查结果 */}
      <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>各 Skill 审查结果</div>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {reviews.map((r) => <SkillReviewRow key={r.skill_id} review={r} />)}
        </div>
      </section>

      {(llm.total_tokens_real || llm.total_tokens_est) && (
        <div className="text-muted text-mono" style={{ fontSize: "var(--text-xs)", padding: "var(--space-2) var(--space-3)" }}>
          Token 用量：{llm.total_tokens_real || llm.total_tokens_est}
          {llm.total_cache_hit_tokens ? ` · 缓存命中 ${llm.total_cache_hit_tokens}` : ""}
        </div>
      )}
    </div>
  );
}

function SkillReviewRow({ review }: { review: SingleStockSkillReview }) {
  const stanceColor =
    review.stance === "support" || review.stance === "strong_support" ? "var(--market-up)" :
    review.stance === "oppose" || review.stance === "veto" ? "var(--market-down)" :
    "var(--muted)";

  if (review.status === "error") {
    return (
      <div style={{ padding: "var(--space-2)", background: "var(--surface-soft)", borderRadius: "var(--radius-sm)", borderLeft: `3px solid var(--market-down)` }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{review.skill_name}</span>
          <span style={{ fontSize: "var(--text-xs)", color: "var(--market-down)" }}>审查失败</span>
        </div>
        <div className="text-muted" style={{ fontSize: "var(--text-xs)", marginTop: 2 }}>{review.error || "未知错误"}</div>
      </div>
    );
  }

  return (
    <div style={{ padding: "var(--space-2)", background: "var(--surface-soft)", borderRadius: "var(--radius-sm)", borderLeft: `3px solid ${stanceColor}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 2 }}>
        <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{review.skill_name}</span>
        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
          {review.score != null && (
            <span className="text-mono" style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{review.score.toFixed(1)}</span>
          )}
          {review.stance && (
            <span style={{ fontSize: "var(--text-xs)", color: stanceColor, fontWeight: 600 }}>{review.stance}</span>
          )}
          {review.action && (
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>{review.action}</span>
          )}
        </div>
      </div>
      {review.summary && (
        <div style={{ fontSize: "var(--text-xs)", color: "var(--ink-secondary)", marginBottom: review.supports?.length || review.risks?.length ? 4 : 0 }}>
          {review.summary}
        </div>
      )}
      {review.supports && review.supports.length > 0 && (
        <div style={{ fontSize: "var(--text-xs)", color: "var(--market-up)", marginBottom: 2 }}>
          ✓ {review.supports.join("；")}
        </div>
      )}
      {review.risks && review.risks.length > 0 && (
        <div style={{ fontSize: "var(--text-xs)", color: "var(--market-down)" }}>
          ⚠ {review.risks.join("；")}
        </div>
      )}
    </div>
  );
}

function RunTimeline({ detail }: { detail: RunDetail }) {
  return (
    <CollapsiblePanel
      collapsedKey="run-timeline"
      kicker="运行时间线"
      title={detail.run_name ?? detail.run_id}
      actions={<span className="tag">{detail.status}</span>}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        <div className="text-mono text-muted" style={{ fontSize: "var(--text-sm)" }}>
          {detail.progress_message || "暂无消息"}
        </div>
        {detail.recommended_count != null && (
          <div style={{ fontSize: "var(--text-sm)" }}>
            产出候选：<strong className="text-mono">{detail.recommended_count}</strong> 只
          </div>
        )}
      </div>
    </CollapsiblePanel>
  );
}
