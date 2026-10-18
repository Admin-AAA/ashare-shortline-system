import { useEffect, useRef, useState } from "react";
import { ArrowLeft, History, Loader2, PlayCircle, RotateCw, Search, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { Preset, RunDetail, SingleStockAnalysis, SingleStockSkillReview, ReplayAnalysisResult, StockMatrixHistoryItem } from "../types";
import { ErrorPage } from "../components/StateViews";
import { Panel } from "../components/Panel";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";
import { useCreateRunMutation, usePresetsQuery, useRunQuery } from "../api/queries/useRunQueries";
import { isActiveRunStatus } from "../utils/display";

/**
 * 运行详情页 —— 从驾驶舱主 CTA 进入
 * 展示阶段时间线、进度、产出摘要。完成后自动引导到候选池。
 */
export function RunDetailPage({ refreshSignal }: { refreshSignal: number }) {
  const { navigate, navigateSection } = useUrlRoute();
  const runId = queryParam("run_id");
  const presetId = queryParam("preset_id");

  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [message, setMessage] = useState("");
  const presetsQuery = usePresetsQuery();
  const runQuery = useRunQuery(runId, refreshSignal);
  const createRunMutation = useCreateRunMutation();
  const presets = presetsQuery.data ?? [];
  const detail = runQuery.data ?? null;
  const error = runQuery.error?.message ?? "";
  const running = createRunMutation.isPending;
  const activeRun = isActiveRunStatus(detail?.status);
  const existingRunLoading = Boolean(runId) && !detail && !error;
  const runActionBusy = running || activeRun || existingRunLoading;
  const runActionLabel = running
    ? "提交中…"
    : existingRunLoading
      ? "正在读取运行状态…"
      : activeRun
        ? (String(detail?.status ?? "").trim().toLowerCase() === "queued" ? "已有任务排队中" : "已有任务运行中")
        : "立即运行";

  useEffect(() => {
    if (!presets.length) return;
    setSelectedPreset((current) => {
      if (current) return current;
      if (presetId && presets.some((item) => item.preset_id === presetId)) return presetId;
      return presets[0]?.preset_id || "";
    });
  }, [presetId, presets]);

  async function startRun() {
    if (!selectedPreset) return;
    setMessage("正在触发推荐运行…");
    try {
      const result = await createRunMutation.mutateAsync({
        preset_id: selectedPreset,
        run_name: `驾驶舱 · 手动推荐`,
      });
      setMessage(`推荐已触发：${result.run.run_id}`);
      navigate("/run", { params: { run_id: result.run.run_id } });
    } catch (e) {
      setMessage(`运行失败：${(e as Error).message}`);
    }
  }

  async function resumeRun() {
    if (!runId) return;
    setMessage("正在核验封印结果…");
    try {
      await api.resumeRun(runId);
      setMessage("已提交完成请求，后台将再次核验并完成运行…");
    } catch (e) {
      setMessage(`完成失败：${(e as Error).message}`);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <button className="btn btn--ghost" onClick={() => navigateSection("dashboard")} style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={14} />
        返回驾驶舱
      </button>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: "var(--space-4)", alignItems: "start" }}>
        <Panel
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
            disabled={runActionBusy || !selectedPreset}
            style={{ padding: "var(--space-3) var(--space-5)" }}
          >
            {runActionBusy ? <Loader2 size={16} className="spin" /> : <PlayCircle size={16} />}
            {runActionLabel}
          </button>

          {message && (
            <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>
              {message}
            </div>
          )}
        </Panel>

        <SingleStockScorePanel key={queryParam("score_code") || "manual"} presets={presets} initialCode={queryParam("score_code") || ""} />
      </div>

      {error && <ErrorPage title="运行详情加载失败" message={error} onRetry={() => window.location.reload()} />}

      {detail && <RunProgressPanel detail={detail} onResume={resumeRun} />}

      {detail && (detail.recommended_count ?? 0) > 0 && runId && <ReplayAnalysisPanel runId={runId} />}
    </div>
  );
}

// ============================================================
// 运行复盘面板 —— 打开已完成 run，看到每只推荐票的实际兑现结果
// 后端 /api/recommendations/runs/{run_id}/replay-analysis 早已实现（纯 DB 读），
// 前端 client.replayAnalysis 也已存在，此前 RunDetailPage 从不调用——本轮补齐（北极星 B「跑出来结果如何」）。
// ============================================================

function ReplayAnalysisPanel({ runId }: { runId: string }) {
  const [data, setData] = useState<ReplayAnalysisResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    api.replayAnalysis(runId)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError((e as Error).message || "复盘加载失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [runId]);

  if (loading) {
    return (
      <Panel kicker="运行复盘" title="推荐兑现情况">
        <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>加载兑现数据…</div>
      </Panel>
    );
  }
  if (error) return <ErrorPage title="复盘加载失败" message={error} />;
  if (!data || data.rows.length === 0) return null;

  const { summary, rows } = data;
  const avgRet = summary.average_net_return;
  const retTone = avgRet >= 0 ? "var(--market-up)" : "var(--market-down)";

  return (
    <Panel
      kicker="运行复盘"
      title="推荐兑现情况"
      actions={
        <span style={{ display: "inline-flex", gap: "var(--space-3)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
          <span>平均净收益 <b style={{ color: retTone, fontFamily: "var(--font-mono)" }}>{avgRet >= 0 ? "+" : ""}{avgRet.toFixed(2)}%</b></span>
          <span>误报率 <b style={{ fontFamily: "var(--font-mono)" }}>{(summary.false_positive_rate * 100).toFixed(0)}%</b></span>
        </span>
      }
    >
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-xs)" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--text-muted)" }}>
              <th style={{ textAlign: "left", padding: "var(--space-2)", fontWeight: 600 }}>代码 / 名称</th>
              <th style={{ textAlign: "right", padding: "var(--space-2)", fontWeight: 600 }}>评分</th>
              <th style={{ textAlign: "right", padding: "var(--space-2)", fontWeight: 600 }}>净收益</th>
              <th style={{ textAlign: "right", padding: "var(--space-2)", fontWeight: 600 }}>持仓</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)", fontWeight: 600 }}>状态</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)", fontWeight: 600 }}>市场环境</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)", fontWeight: 600 }}>买点规则</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const ret = r.net_return_pct;
              const retColor = ret == null ? "var(--text-muted)" : ret >= 0 ? "var(--market-up)" : "var(--market-down)";
              const retText = ret == null ? "结算中" : `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%`;
              // 行内状态以「实际兑现结果」为准（outcome_label / 净收益），避免把盈利票误标为误报
              const statusLabel = r.outcome_label
                ? r.outcome_label
                : (ret == null ? "待结算" : (ret >= 0 ? "盈利" : "亏损"));
              const statusTone = r.outcome_label === "success"
                ? "up"
                : r.outcome_label === "failed"
                  ? "down"
                  : (ret == null ? "muted" : (ret >= 0 ? "up" : "down"));
              return (
                <tr key={r.recommendation_id} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ textAlign: "left", padding: "var(--space-2)" }}>
                    <span className="text-mono" style={{ color: "var(--ink)", fontWeight: 600 }}>{r.symbol}</span>
                    <span style={{ marginLeft: "var(--space-2)", color: "var(--ink-secondary)" }}>{r.name}</span>
                  </td>
                  <td style={{ textAlign: "right", padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: "var(--ink)" }}>
                    {r.final_score != null ? r.final_score.toFixed(1) : "—"}
                  </td>
                  <td style={{ textAlign: "right", padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: retColor, fontWeight: 600 }}>
                    {retText}
                  </td>
                  <td style={{ textAlign: "right", padding: "var(--space-2)", fontFamily: "var(--font-mono)", color: "var(--ink-secondary)" }}>
                    {r.holding_days != null ? `${r.holding_days}天` : "—"}
                  </td>
                  <td style={{ textAlign: "left", padding: "var(--space-2)" }}>
                    <ReplayStatusTag label={statusLabel} tone={statusTone} />
                  </td>
                  <td style={{ textAlign: "left", padding: "var(--space-2)", color: "var(--ink-secondary)" }}>
                    {[r.market_regime, r.trend_status].filter(Boolean).join(" · ") || "—"}
                  </td>
                  <td
                    style={{ textAlign: "left", padding: "var(--space-2)", color: "var(--text-muted)", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    title={r.trigger_condition_text || undefined}
                  >
                    {r.trigger_condition_text || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {summary.false_positive_count > 0 && (
        <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginTop: "var(--space-2)" }}>
          误报 {summary.false_positive_count} 只
          {summary.reason_counts?.length
            ? ` · 主因：${summary.reason_counts.slice(0, 2).map((x) => x.reason).filter(Boolean).join("、")}`
            : ""}
        </div>
      )}
    </Panel>
  );
}

function ReplayStatusTag({ label, tone }: { label: string; tone: "up" | "down" | "muted" }) {
  const color = tone === "up" ? "var(--market-up)" : tone === "down" ? "var(--market-down)" : "var(--text-muted)";
  return <span style={{ color, fontWeight: 600, fontSize: "var(--text-xs)" }}>{label}</span>;
}

// ============================================================
// 单股票评分面板
// ============================================================

function SingleStockScorePanel({ presets, initialCode = "" }: { presets: Preset[]; initialCode?: string }) {
  const [code, setCode] = useState(initialCode);
  const autoScored = useRef(false);
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [includeLlm, setIncludeLlm] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SingleStockAnalysis | null>(null);
  const [error, setError] = useState("");

  // 历史列表：按当前 code 拉 analysis_type=single_stock 的评分历史
  const [history, setHistory] = useState<StockMatrixHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyCode, setHistoryCode] = useState("");
  // 回放某条历史：加载其完整 result_json 后渲染
  const [replayId, setReplayId] = useState<number | null>(null);

  useEffect(() => {
    setSelectedPreset((current) => current || presets[0]?.preset_id || "");
  }, [presets]);

  async function loadHistory(codeToFetch: string) {
    const trimmed = codeToFetch.trim();
    if (!trimmed) {
      setHistory([]);
      setHistoryCode("");
      return;
    }
    setHistoryLoading(true);
    setHistoryCode(trimmed);
    try {
      const res = await api.singleStockHistory(trimmed, 1, 20);
      setHistory(res.items);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }

  // code 变化时（失焦或评分后）拉历史；这里用输入即时拉取会过于频繁，
  // 改为：code 非空且与已加载历史不同时，在评分/失焦触发。下面 effect 兜底首次进入。
  useEffect(() => {
    void loadHistory(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 来自 ⌘K「立即评分」：/run?score_code=xxxxxx —— 预设就绪后自动评分一次
  useEffect(() => {
    if (autoScored.current || !/^\d{6}$/.test(initialCode) || !selectedPreset) return;
    autoScored.current = true;
    void runScore();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCode, selectedPreset]);

  async function runScore() {
    const trimmed = code.trim();
    if (!trimmed) {
      setError("请输入股票代码");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    setReplayId(null);
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
        void loadHistory(trimmed); // 评分成功后刷新历史
      }
    } catch (e) {
      setError((e as Error).message || "单股票评分失败");
    } finally {
      setLoading(false);
    }
  }

  async function loadReplay(id: number) {
    setReplayId(id);
    setResult(null);
    setError("");
    try {
      const detail = await api.matrixRecordDetail(id);
      const replayed = detail.result as unknown as SingleStockAnalysis;
      if (replayed && replayed.status === "ok") {
        setResult(replayed);
      } else {
        setError("该历史记录无法回放");
        setReplayId(null);
      }
    } catch (e) {
      setError((e as Error).message || "历史回放失败");
      setReplayId(null);
    }
  }

  return (
    <Panel
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
        输入股票代码，以该股票作为唯一输入执行正式推荐管线；预筛、技能门控、深审、聚合与入池规则均与批量推荐一致。
      </div>

      <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center", flexWrap: "wrap" }}>
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") runScore(); }}
          onBlur={(e) => {
            if (e.target.value.trim() && e.target.value.trim() !== historyCode) void loadHistory(e.target.value.trim());
          }}
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

      <SingleStockHistoryList
        items={history}
        loading={historyLoading}
        code={historyCode}
        replayId={replayId}
        onReplay={loadReplay}
      />
    </Panel>
  );
}

function SingleStockHistoryList({
  items,
  loading,
  code,
  replayId,
  onReplay,
}: {
  items: StockMatrixHistoryItem[];
  loading: boolean;
  code: string;
  replayId: number | null;
  onReplay: (id: number) => void;
}) {
  if (!code) return null;
  if (loading && items.length === 0) {
    return (
      <div style={{ marginTop: "var(--space-2)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>
          <History size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          评分历史
        </div>
        <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>加载历史…</div>
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div style={{ marginTop: "var(--space-2)" }}>
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>
          <History size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          评分历史
        </div>
        <div className="text-muted" style={{ fontSize: "var(--text-sm)" }}>暂无历史评分记录</div>
      </div>
    );
  }

  return (
    <div style={{ marginTop: "var(--space-2)" }}>
      <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>
        <History size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
        评分历史 · {code}（{items.length}）
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        {items.map((h) => {
          const active = replayId === h.id;
          return (
            <button
              key={h.id}
              onClick={() => onReplay(h.id)}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr auto auto",
                gap: "var(--space-3)",
                alignItems: "center",
                textAlign: "left",
                padding: "var(--space-2) var(--space-3)",
                background: active ? "var(--surface)" : "var(--surface-soft)",
                border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                fontFamily: "inherit",
                color: "var(--ink)",
              }}
            >
              <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
                {h.trade_date}
              </span>
              <span className="text-muted" style={{ fontSize: "var(--text-xs)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {h.created_at ? new Date(h.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
              </span>
              <span className="text-mono" style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--accent)" }}>
                {h.final_score != null ? h.final_score.toFixed(1) : "—"}
              </span>
              <span className="text-muted text-mono" style={{ fontSize: "var(--text-xs)" }}>
                {h.skill_count > 1 ? `${h.skill_count}skill` : "规则"}
              </span>
            </button>
          );
        })}
      </div>
      <div className="text-muted" style={{ fontSize: "var(--text-xs)", marginTop: "var(--space-2)" }}>
        点击历史记录可回放该次完整评分结果。
      </div>
    </div>
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
          <ScoreMetric label="规则分（出票依据）" value={result.final_score} />
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>推荐结果</span>
            <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: result.recommended ? "var(--market-up)" : "var(--ink-secondary)" }}>
              {result.recommended ? "已入选" : "未入选"}
            </span>
          </div>
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
        <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>LLM 提示 · 仅供参考，不影响出票</div>
        <div style={{ color: "var(--market-down)", fontSize: "var(--text-sm)" }}>分析失败：{llm.error || "未知错误"}</div>
      </section>
    );
  }

  // 正式管线 Skill 深审结果（即使后续因去重/分数线未入池，也应完整展示）
  if (llm.skill_reviews && llm.skill_reviews.length > 0) {
    return <PresetLlmSection llm={llm} />;
  }

  const stanceColor =
    llm.stance === "support" ? "var(--market-up)" :
    llm.stance === "oppose" ? "var(--market-down)" :
    "var(--muted)";

  return (
    <section className="panel" style={{ margin: 0, padding: "var(--space-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
        <div className="panel__kicker">LLM 提示 · 仅供参考，不影响出票</div>
        {llm.elapsed_ms != null && (
          <span className="text-muted text-mono" style={{ fontSize: "var(--text-xs)" }}>{llm.elapsed_ms}ms</span>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "var(--space-3)", marginBottom: "var(--space-3)" }}>
        <ScoreMetric label="规则分（出票依据）" value={llm.rule_score ?? llm.final_score} />
        <ScoreMetric label="LLM 参考分" value={llm.score} />
        <ScoreMetric label="LLM 建议调整（未生效）" value={llm.llm_adjustment} />
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
          <div className="panel__kicker">管线聚合评分{llm.preset_name ? ` · ${llm.preset_name}` : ""}</div>
          {llm.elapsed_ms != null && (
            <span className="text-muted text-mono" style={{ fontSize: "var(--text-xs)" }}>{llm.elapsed_ms}ms</span>
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "var(--space-3)" }}>
          <ScoreMetric label="规则分（出票依据）" value={llm.final_score} />
          <ScoreMetric label="Agent审查分（参考）" value={llm.agent_review_score} />
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

const RUN_STATUS_META: Record<string, { label: string; color: string }> = {
  queued: { label: "排队中", color: "var(--muted-strong)" },
  running: { label: "运行中", color: "var(--accent)" },
  completed: { label: "已完成", color: "var(--market-up)" },
  failed: { label: "失败", color: "var(--market-down)" },
  interrupted: { label: "已中断", color: "var(--warning)" },
};

function runPhaseLabel(phase?: string | null): string {
  const map: Record<string, string> = {
    queued: "排队等待",
    base_filter: "基础筛选",
    data_prep: "数据准备",
    feature_build: "特征构建",
    skill_review: "Skill 审查",
    scoring: "评分聚合",
    gate: "质量闸门",
    interrupted: "已中断",
    failed: "失败",
    completed: "完成",
  };
  if (!phase) return "";
  return map[phase] ?? phase;
}

function formatHeartbeatAge(sec?: number | null): string {
  if (sec == null) return "";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  const h = Math.floor(m / 60);
  return `${h} 时 ${m % 60} 分`;
}

function RunStatusTag({ status }: { status: string }) {
  const meta = RUN_STATUS_META[status] ?? { label: status, color: "var(--muted-strong)" };
  const active = isActiveRunStatus(status);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "var(--text-xs)", color: meta.color, fontWeight: 600 }}>
      {active && <span className="run-progress-spin" style={{ width: 7, height: 7, borderRadius: "50%", background: meta.color, display: "inline-block" }} />}
      {meta.label}
    </span>
  );
}

function RunProgressPanel({ detail, onResume }: { detail: RunDetail; onResume?: () => void }) {
  const active = isActiveRunStatus(detail.status);
  const pct = detail.progress_pct;
  const hasPct = typeof pct === "number" && pct >= 0;
  const derivedPct = !hasPct && detail.progress_total != null && detail.progress_current != null
    ? Math.min(100, Math.round((detail.progress_current / detail.progress_total) * 100))
    : null;
  const effectivePct = hasPct ? pct! : derivedPct;
  const indeterminate = active && effectivePct == null;
  const barWidth = effectivePct != null ? `${Math.max(2, Math.min(100, effectivePct))}%` : "100%";
  const stale = !!detail.is_stale;
  const resumable = detail.can_resume === true;
  const degradedWatchlist = Array.isArray(detail.result_summary?.degraded_watchlist)
    ? detail.result_summary.degraded_watchlist as Array<Record<string, unknown>>
    : [];

  return (
    <Panel
      kicker="运行进度"
      title={detail.run_name ?? detail.run_id}
      actions={<RunStatusTag status={detail.status} />}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        {active && (
          <div className="run-progress-bar" role="progressbar" aria-valuenow={effectivePct ?? undefined}>
            <div
              className={`run-progress-bar__fill${indeterminate ? " run-progress-bar__fill--indeterminate" : ""}`}
              style={{ width: barWidth }}
            />
          </div>
        )}

        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)", alignItems: "baseline" }}>
          {detail.progress_phase && (
            <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--ink)" }}>
              {runPhaseLabel(detail.progress_phase)}
            </span>
          )}
          {effectivePct != null && (
            <span className="text-mono" style={{ fontSize: "var(--text-sm)", color: "var(--accent)" }}>{effectivePct}%</span>
          )}
          {detail.progress_total ? (
            <span className="text-muted text-mono" style={{ fontSize: "var(--text-xs)" }}>
              {detail.progress_current}/{detail.progress_total}
            </span>
          ) : null}
        </div>

        <div className="text-mono text-muted" style={{ fontSize: "var(--text-sm)", minHeight: 18 }}>
          {active ? (detail.progress_message || "运行中…") : (detail.progress_message || "暂无消息")}
        </div>

        {degradedWatchlist.length > 0 && (
          <div style={{ border: "1px solid var(--warning)", borderRadius: "var(--radius-md)", padding: "var(--space-3)", background: "var(--surface-soft)" }}>
            <div style={{ color: "var(--warning)", fontWeight: 700, fontSize: "var(--text-sm)", marginBottom: "var(--space-2)" }}>
              降级观察 · 非正式推荐
            </div>
            <div className="text-muted" style={{ fontSize: "var(--text-xs)", marginBottom: "var(--space-2)" }}>
              Skill Agent 全部失败，以下候选仅保留规则结果，不进入推荐收益统计。
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
              {degradedWatchlist.map((item, index) => (
                <span key={`${String(item.code ?? "unknown")}-${index}`} className="text-mono" style={{ fontSize: "var(--text-xs)" }}>
                  {String(item.code ?? "—")} {String(item.name ?? "")} · {Number(item.final_score ?? 0).toFixed(1)}
                </span>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)", alignItems: "center", fontSize: "var(--text-xs)" }}>
          {detail.heartbeat_at && (
            <span className="text-muted">心跳 {formatHeartbeatAge(detail.heartbeat_age_seconds)}前</span>
          )}
          {stale && (
            <span style={{ color: "var(--warning)", fontWeight: 600 }}>⚠ 心跳超时（后台可能中断）</span>
          )}
          {detail.recommended_count != null && detail.recommended_count > 0 && (
            <span style={{ fontSize: "var(--text-sm)" }}>
              产出候选：<strong className="text-mono">{detail.recommended_count}</strong> 只
            </span>
          )}
          {!active && detail.recommended_count === 0 && (
            <span style={{ fontSize: "var(--text-sm)", color: "var(--warning)" }}>
              ⚠ 空仓/无机会：{detail.result_summary?.no_opportunity_reason ?? "全部候选未过分数线"}
            </span>
          )}
        </div>

        {resumable && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
              <button className="btn btn--primary" onClick={onResume} style={{ padding: "var(--space-2) var(--space-4)" }}>
                <RotateCw size={15} />
                核验封印结果并完成运行
              </button>
              <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>结果已持久封印，仅完成生命周期与溯源，不重跑任何业务阶段。</span>
            </div>
          </div>
        )}

        {!resumable && detail.status === "interrupted" && (
          <div style={{ border: "1px solid var(--warning)", borderRadius: "var(--radius-md)", padding: "var(--space-3)", background: "var(--surface-soft)" }}>
            <div style={{ color: "var(--warning)", fontWeight: 700, fontSize: "var(--text-sm)", marginBottom: "var(--space-2)" }}>
              中断运行无法自动完成
            </div>
            <div className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
              {detail.resume_block_reason || "缺少已封印的推荐集合，无法安全完成该运行。请用新运行替代，不要重放历史。"}
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}
