import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Plug, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import type { GatewayDataset, GatewayProvider, GatewaySwitch, GatewayTopology, ProviderTestResult } from "../types";

const MODE_LABEL: Record<GatewayDataset["mode"], string> = {
  fill: "逐只补齐",
  first: "首个命中",
  chain: "按序回退",
  router: "按接口路由",
};
const swKey = (s: GatewaySwitch) => `${s.source}/${s.source_type}`;
const hhmm = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

/**
 * 数据源管理 —— 按数据网关的数据类型分列：每列一个数据类型，列内按回退顺序列出 provider，
 * 回填 live 熔断状态（后端 + Prefect worker 共享），可直接启停 provider 的开关。
 * 开关即 data_source_config 的 (source, source_type) 行，一个开关可能被多个 provider 共用，卡片上会标出。
 */
export function DataSourcesSection() {
  const [topo, setTopo] = useState<GatewayTopology | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, ProviderTestResult>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTopo(await api.dataGateway());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  // 开关 → 共用它的「数据类型 · provider」列表（提示：禁用会同时影响这些位置）。
  const usage = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const d of topo?.datasets ?? [])
      for (const p of d.providers)
        for (const s of p.switches) m.set(swKey(s), [...(m.get(swKey(s)) ?? []), `${d.label}·${p.label}`]);
    return m;
  }, [topo]);

  async function run(id: string, fn: () => Promise<GatewayTopology | void>) {
    setBusy(id);
    setError(null);
    try {
      const next = await fn();
      if (next) setTopo(next);
      else await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function toggle(p: GatewayProvider, id: string) {
    const target = !p.switches.every((s) => s.enabled);
    return run(id, async () => {
      let last: GatewayTopology | undefined;
      for (const s of p.switches) last = await api.setGatewaySwitch(s.source, s.source_type, target);
      return last;
    });
  }

  function move(list: GatewayProvider[], idx: number, dir: -1 | 1) {
    const names = list.map((p) => p.name);
    [names[idx], names[idx + dir]] = [names[idx + dir], names[idx]];
    return run(`reorder`, async () => {
      await api.reorderProviders(names);
    });
  }

  async function test(name: string) {
    setBusy(`test:${name}`);
    try {
      const res = await api.testProvider(name);
      setTests((m) => ({ ...m, [name]: res }));
    } catch (e: unknown) {
      setTests((m) => ({ ...m, [name]: { provider_name: name, ok: false, status: "degraded", error: e instanceof Error ? e.message : String(e) } }));
    } finally {
      setBusy(null);
    }
  }

  const openCount = (topo?.datasets ?? []).flatMap((d) => d.providers).filter((p) => p.breaker.state === "open").length;

  return (
    <div className="panel">
      <div className="panel__header">
        <div>
          <span className="panel__kicker">数据网关</span>
          <h3 className="panel__title" style={{ fontSize: "var(--text-xl)" }}>数据源管理</h3>
        </div>
        <button className="cfg-btn" onClick={load} disabled={loading}>
          {loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}
          刷新
        </button>
      </div>

      <p className="gw-intro">
        每列是一个数据类型，列内自上而下即网关的回退顺序。熔断状态取自实盘进程（后端与 Prefect）共享的熔断库：
        <span className="gw-dot gw-dot--open" /> 熔断中（到点前不再请求）
        <span className="gw-dot gw-dot--half_open" /> 待探测（冷却已过，下一次请求试探）
        <span className="gw-dot gw-dot--closed" /> 正常。禁用 / 启用写入数据源开关，各进程 5 秒内生效、无需重启。
        {openCount > 0 && <b className="gw-alert"> 当前 {openCount} 个数据源熔断中。</b>}
      </p>

      {error && <div className="gw-error">{error}</div>}

      {!topo ? (
        <div className="muted small" style={{ padding: "var(--space-4)" }}>{loading ? "加载中…" : "未加载"}</div>
      ) : (
        <div className="gw-board">
          {topo.datasets.map((d) => (
            <section key={d.key} className="gw-col">
              <header className="gw-col__head">
                <b>{d.label}</b>
                <span className="gw-chip">{MODE_LABEL[d.mode]}</span>
                {!d.routed && <span className="gw-chip gw-chip--muted" title={d.note ?? ""}>未切网关</span>}
                <code className="gw-col__key">{d.key}</code>
              </header>
              {d.note && d.routed && <p className="gw-col__note">{d.note}</p>}
              <ol className="gw-list">
                {d.providers.map((p, i) => {
                  const id = `${d.key}:${p.name}`;
                  const enabled = p.switches.every((s) => s.enabled);
                  const shared = Array.from(new Set(p.switches.flatMap((s) => usage.get(swKey(s)) ?? []))).filter((u) => u !== `${d.label}·${p.label}`);
                  const state = !enabled ? "disabled" : p.breaker.state;
                  const router = d.mode === "router";
                  const res = tests[p.name];
                  return (
                    <li key={p.name} className={`gw-src gw-src--${state}${p.active ? "" : " gw-src--inactive"}`}>
                      <div className="gw-src__row">
                        <span className="gw-src__idx">{i + 1}</span>
                        <span className={`gw-dot gw-dot--${state}`} />
                        <span className="gw-src__name" title={p.name}>{p.label}</span>
                        {router && (
                          <span className="gw-src__ord">
                            <button className="cfg-btn gw-icon" disabled={busy !== null || i === 0} onClick={() => move(d.providers, i, -1)} title="上移（更优先）"><ArrowUp size={12} /></button>
                            <button className="cfg-btn gw-icon" disabled={busy !== null || i === d.providers.length - 1} onClick={() => move(d.providers, i, 1)} title="下移"><ArrowDown size={12} /></button>
                          </span>
                        )}
                        {router && (
                          <button className="cfg-btn gw-btn" disabled={busy !== null} onClick={() => test(p.name)} title="测试连通性">
                            {busy === `test:${p.name}` ? <Loader2 size={12} className="spin" /> : <Plug size={12} />}
                          </button>
                        )}
                        {p.switches.length > 0 ? (
                          <button className={enabled ? "cfg-btn gw-btn" : "cfg-btn cfg-btn--primary gw-btn"} disabled={busy !== null} onClick={() => toggle(p, id)}>
                            {busy === id && <Loader2 size={12} className="spin" />}
                            {enabled ? "禁用" : "启用"}
                          </button>
                        ) : (
                          <span className="gw-src__tag" title="没有独立开关，随 Tushare 路由（Promax/QuantData）启停">随路由</span>
                        )}
                      </div>
                      <div className="gw-src__meta">
                        <StateText state={state} items={p.breaker.items} />
                        {!p.active && <span>· {p.inactive_reason}</span>}
                      </div>
                      {p.breaker.items.length > 0 && (
                        <div className="gw-src__brk">
                          {p.breaker.items.slice(0, router ? 6 : 3).map((b) => (
                            <Fragment key={b.key}>
                              <span className={`gw-dot gw-dot--${b.state}`} />
                              <code>{b.key.replace(/^tushare:[a-z]+:|^gw:[a-z_0-9]+:/, "")}</code>
                              <span>{b.state === "open" ? `至 ${hhmm(b.open_until)}` : "待探测"}{b.reason ? ` · ${b.reason}` : ""}</span>
                            </Fragment>
                          ))}
                          {p.breaker.items.length > (router ? 6 : 3) && <span className="gw-more">… 共 {p.breaker.items.length} 个接口</span>}
                        </div>
                      )}
                      {shared.length > 0 && <div className="gw-src__shared" title={shared.join("\n")}>共用开关：{shared.join("、")}</div>}
                      {res && <div className={res.ok ? "gw-src__ok" : "gw-src__fail"}>{res.ok ? "连接正常" : `连接失败：${res.error || "未知错误"}`}{res.latency_ms != null && ` · ${res.latency_ms} ms`}</div>}
                    </li>
                  );
                })}
              </ol>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function StateText({ state, items }: { state: string; items: GatewayProvider["breaker"]["items"] }) {
  if (state === "disabled") return <span>已禁用</span>;
  if (state === "closed") return <span>正常</span>;
  const open = items.filter((b) => b.state === "open");
  if (state === "open") {
    const until = Math.max(...open.map((b) => b.open_until));
    return <span className="gw-text--open">熔断中 · {open.length} 项，最晚至 {hhmm(until)}</span>;
  }
  return <span className="gw-text--half_open">待探测 · {items.length} 项冷却已过</span>;
}
