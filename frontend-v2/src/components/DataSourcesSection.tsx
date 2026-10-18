import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Plug, RefreshCw, Server } from "lucide-react";
import { api } from "../api/client";
import type { DataSourceProvider, ProviderTestResult } from "../types";

// 参与路由调序的 pro provider（realtime_quote 是独立爬虫，不在路由链，不调序）。
const ROUTABLE = new Set(["promax", "quantdata"]);

/**
 * 数据源管理 —— 配置页 Tab。
 * 对已接入的 tushare 系 provider（promax / quantdata 等）
 * 提供操作：启用 / 禁用（运行时即时生效，不动 config）/ 测试连通性 / 调整路由优先级。
 * 接入新数据源仍走开发（注册进代码/config），本页只做管理，不做动态新增。
 */
export function DataSourcesSection() {
  const [providers, setProviders] = useState<DataSourceProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [reordering, setReordering] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, ProviderTestResult>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.dataSourceProviders();
      setProviders(sortProviders(data.providers));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 可路由 provider 按优先级排前，realtime_quote 等不可路由的排后（保持原序）。
  function sortProviders(list: DataSourceProvider[]): DataSourceProvider[] {
    const routable = list.filter((p) => ROUTABLE.has(p.provider_name)).sort((a, b) => a.priority - b.priority);
    const rest = list.filter((p) => !ROUTABLE.has(p.provider_name));
    return [...routable, ...rest];
  }

  async function handleToggle(p: DataSourceProvider) {
    setBusyName(p.provider_name);
    setError(null);
    try {
      await api.setProviderEnabled(p.provider_name, !p.enabled);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyName(null);
    }
  }

  async function handleMove(name: string, dir: -1 | 1) {
    const routable = providers.filter((p) => ROUTABLE.has(p.provider_name));
    const idx = routable.findIndex((p) => p.provider_name === name);
    const target = idx + dir;
    if (idx < 0 || target < 0 || target >= routable.length) return;
    const next = [...routable];
    [next[idx], next[target]] = [next[target], next[idx]];
    setReordering(true);
    setError(null);
    try {
      const data = await api.reorderProviders(next.map((p) => p.provider_name));
      setProviders(sortProviders(data.providers));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReordering(false);
    }
  }

  async function handleTest(p: DataSourceProvider) {
    setBusyName(p.provider_name);
    try {
      const res = await api.testProvider(p.provider_name);
      setTestResults((m) => ({ ...m, [p.provider_name]: res }));
    } catch (e: unknown) {
      setTestResults((m) => ({
        ...m,
        [p.provider_name]: {
          provider_name: p.provider_name,
          ok: false,
          status: "degraded",
          error: e instanceof Error ? e.message : String(e),
        },
      }));
    } finally {
      setBusyName(null);
    }
  }

  // 可路由 provider 在数组中的名次（用于上下移按钮的禁用态）。
  const routableOrder = providers.filter((p) => ROUTABLE.has(p.provider_name)).map((p) => p.provider_name);

  return (
    <div className="panel">
      <div className="panel__header">
        <div>
          <span className="panel__kicker">行情上游</span>
          <h3 className="panel__title" style={{ fontSize: "var(--text-xl)" }}>数据源管理</h3>
        </div>
        <button className="cfg-btn" onClick={load} disabled={loading}>
          {loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}
          刷新
        </button>
      </div>

      <p style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)", marginTop: "calc(-1 * var(--space-2))", marginBottom: "var(--space-3)", lineHeight: 1.6 }}>
        已接入的行情数据源，按优先级参与同步 fallback（某源故障自动切下一个）。可启停（即时生效、无需重启）、可测试连通性、可用 ↑↓ 调整路由优先级（排在最上的为通用同步主源）。接入新源需开发注册。
      </p>

      {error && (
        <div style={{ marginBottom: "var(--space-3)", padding: "var(--space-3)", borderRadius: 8, fontSize: "var(--text-sm)", background: "color-mix(in srgb, var(--warning) 14%, transparent)", color: "var(--warning)" }}>
          {error}
        </div>
      )}

      {loading && providers.length === 0 ? (
        <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)", padding: "var(--space-4)" }}>加载中…</div>
      ) : (
        <div style={{ display: "grid", gap: "var(--space-4)" }}>
          {groupProviders(providers).map((g) => (
          <section key={g.key}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, margin: "4px 0 8px" }}>
              <b style={{ fontSize: 14 }}>{g.label}</b>
              <span className="muted small">{g.items.length} 个 · {g.hint}</span>
            </div>
          <div style={{ display: "grid", gap: "var(--space-3)" }}>
          {g.items.map((p) => {
            const res = testResults[p.provider_name];
            const busy = busyName === p.provider_name;
            const routable = ROUTABLE.has(p.provider_name);
            const rank = routableOrder.indexOf(p.provider_name);
            return (
              <div
                key={p.provider_name}
                style={{
                  padding: "var(--space-3) var(--space-4)",
                  background: "var(--surface-1)",
                  borderRadius: 8,
                  border: "1px solid var(--line)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
                  {routable ? (
                    <span style={{ display: "inline-flex", flexDirection: "column", gap: 2 }} title="调整路由优先级（越小越优先）">
                      <button
                        className="cfg-btn"
                        style={{ padding: "1px 4px", lineHeight: 1 }}
                        onClick={() => handleMove(p.provider_name, -1)}
                        disabled={reordering || busy || rank <= 0}
                        title="上移（更优先）"
                      >
                        <ArrowUp size={13} />
                      </button>
                      <button
                        className="cfg-btn"
                        style={{ padding: "1px 4px", lineHeight: 1 }}
                        onClick={() => handleMove(p.provider_name, 1)}
                        disabled={reordering || busy || rank < 0 || rank >= routableOrder.length - 1}
                        title="下移（更靠后）"
                      >
                        <ArrowDown size={13} />
                      </button>
                    </span>
                  ) : (
                    <span
                      style={{
                        minWidth: 26,
                        height: 26,
                        borderRadius: 999,
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "var(--surface-2)",
                        color: "var(--text-muted)",
                        fontSize: "var(--text-xs)",
                        fontWeight: 700,
                      }}
                      title="独立爬虫，不参与路由调序"
                    >
                      —
                    </span>
                  )}

                  <Server size={16} className="text-muted" />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontWeight: 600 }}>
                      {p.provider_name}
                      {routable && rank === 0 && (
                        <span style={{ marginLeft: "var(--space-2)", fontSize: "var(--text-xs)", fontWeight: 500, color: "var(--accent)" }}>主源</span>
                      )}
                    </div>
                    <div style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>
                      {p.note || p.source}
                    </div>
                  </div>

                  <StatusBadge status={p.status} />
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      padding: "2px 10px",
                      borderRadius: 999,
                      color: p.enabled ? "var(--positive)" : "var(--muted)",
                      background: p.enabled ? "color-mix(in srgb, var(--positive) 14%, transparent)" : "var(--surface-2)",
                    }}
                  >
                    {p.enabled ? "启用" : "停用"}
                  </span>

                  <div style={{ display: "flex", gap: "var(--space-2)", marginLeft: "auto" }}>
                    <button className="cfg-btn" onClick={() => handleTest(p)} disabled={busy}>
                      {busy ? <Loader2 size={14} className="spin" /> : <Plug size={14} />}
                      测试
                    </button>
                    <button
                      className={p.enabled ? "cfg-btn" : "cfg-btn cfg-btn--primary"}
                      onClick={() => handleToggle(p)}
                      disabled={busy}
                    >
                      {busy ? <Loader2 size={14} className="spin" /> : null}
                      {p.enabled ? "禁用" : "启用"}
                    </button>
                  </div>
                </div>

                {p.apis && p.apis.length > 0 && (
                  <div style={{ marginTop: "var(--space-2)", display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    {p.apis.map((a) => (
                      <span
                        key={a}
                        style={{
                          fontSize: "var(--text-xs)",
                          padding: "2px 8px",
                          borderRadius: 999,
                          background: "var(--surface-2)",
                          color: "var(--text-muted)",
                        }}
                      >
                        {a}
                      </span>
                    ))}
                  </div>
                )}

                <div style={{ marginTop: "var(--space-2)", display: "flex", gap: "var(--space-4)", flexWrap: "wrap", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                  {p.last_success_at && <span>最近成功 {p.last_success_at}</span>}
                  {p.last_failure_at && <span>最近失败 {p.last_failure_at}</span>}
                  {p.latency_ms != null && <span>延迟 {p.latency_ms} ms</span>}
                  {p.last_api && <span>last_api {p.last_api}</span>}
                </div>

                {p.last_error && (
                  <div style={{ marginTop: "var(--space-2)", fontSize: "var(--text-xs)", color: "var(--warning)" }}>
                    {p.last_error}
                  </div>
                )}

                {res && (
                  <div
                    style={{
                      marginTop: "var(--space-3)",
                      padding: "var(--space-2) var(--space-3)",
                      borderRadius: 6,
                      fontSize: "var(--text-sm)",
                      background: res.ok ? "color-mix(in srgb, var(--positive) 8%, transparent)" : "color-mix(in srgb, var(--warning) 8%, transparent)",
                      color: res.ok ? "var(--positive)" : "var(--warning)",
                    }}
                  >
                    {res.ok ? "连接正常" : `连接失败：${res.error || "未知错误"}`}
                    {res.latency_ms != null && ` · ${res.latency_ms} ms`}
                  </div>
                )}
              </div>
            );
          })}
          </div>
          </section>
          ))}
        </div>
      )}
    </div>
  );
}

/** 分组：Tushare Pro 兼容网关（可路由、参与优先级 fallback）vs 行情/K线直连源。分组完全由 API 返回的 provider 推导。 */
function groupProviders(list: DataSourceProvider[]) {
  const isTs = (p: DataSourceProvider) => ROUTABLE.has(p.provider_name) || /tushare/i.test(`${p.provider_name} ${p.source} ${p.source_type} ${p.layer ?? ""}`);
  return [
    { key: "quote", label: "行情 / K线", hint: "实时行情与日线直连源", items: list.filter((p) => !isTs(p)) },
    { key: "ts", label: "Tushare Pro 系", hint: "按优先级自上而下 fallback，↑↓ 调整", items: list.filter(isTs) },
  ].filter((g) => g.items.length > 0);
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string; bg: string }> = {
    healthy: { label: "健康", color: "var(--positive)", bg: "color-mix(in srgb, var(--positive) 14%, transparent)" },
    degraded: { label: "降级", color: "var(--warning)", bg: "color-mix(in srgb, var(--warning) 14%, transparent)" },
    untested: { label: "未测试", color: "var(--muted)", bg: "var(--surface-2)" },
    disabled: { label: "已禁用", color: "var(--muted)", bg: "var(--surface-2)" },
  };
  const s = map[status] || map.untested;
  return (
    <span style={{ fontSize: "var(--text-xs)", padding: "2px 10px", borderRadius: 999, color: s.color, background: s.bg }}>
      {s.label}
    </span>
  );
}
