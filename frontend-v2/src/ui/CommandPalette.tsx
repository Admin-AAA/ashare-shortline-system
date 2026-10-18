import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useUrlRoute } from "../app/useUrlRoute";
import { setHorizon } from "../app/useHorizon";

type Item = { key: string; label: string; hint?: string; group: string; run: () => void };

/** ⌘K：近期推荐股票（/api/recommendations/history）+ 页面跳转 + 快捷操作 */
export function CommandPalette({ onClose, onRun, onToggleTheme }: { onClose: () => void; onRun: () => void; onToggleTheme: () => void }) {
  const { navigate } = useUrlRoute();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const hist = useQuery({ queryKey: ["cmdk-history"], queryFn: () => api.recommendationHistory({ limit: 200 }), staleTime: 120_000 });
  const items = useMemo<Item[]>(() => {
    const k = q.trim().toUpperCase();
    const seen = new Set<string>();
    const stocks: Item[] = [];
    for (const it of hist.data?.items ?? []) {
      if (seen.has(it.code)) continue;
      if (k && !it.code.includes(k) && !it.name.toUpperCase().includes(k)) continue;
      seen.add(it.code);
      stocks.push({ key: `s${it.code}`, group: "近期推荐股票", label: `${it.name} ${it.code}`, hint: `${it.trade_date} · ${it.setup_type ?? ""}`, run: () => navigate("/history", { params: { code: it.code } }) });
      if (stocks.length >= 6) break;
    }
    const acts: Item[] = [];
    if (/^\d{6}$/.test(k)) acts.push({ key: "score", group: "快捷操作", label: `立即评分 ${k}`, hint: "analyzeStock", run: () => navigate("/run", { params: { score_code: k } }) });
    acts.push(
      { key: "run", group: "快捷操作", label: "▶ 运行推荐", run: onRun },
      { key: "theme", group: "快捷操作", label: "切换深 / 浅色", run: onToggleTheme },
      { key: "hs", group: "周期", label: "切换到短线", run: () => setHorizon("short") },
      { key: "hm", group: "周期", label: "切换到中线", run: () => setHorizon("mid") },
      { key: "hl", group: "周期", label: "切换到长线", run: () => setHorizon("long") },
    );
    const pages: Item[] = [
      ["/", "今日"], ["/review", "复盘 · 按批次"], ["/history", "复盘 · 交易明细"], ["/records", "复盘 · 运行记录"], ["/strategies", "策略"], ["/config", "系统 · 配置"], ["/metrics", "系统 · 诊断 / 研究"],
    ].filter(([, l]) => !k || l.includes(q.trim())).map(([p, l]) => ({ key: p, group: "页面", label: l, run: () => navigate(p) }));
    return [...stocks, ...acts.filter((a) => !k || /^\d{6}$/.test(k) || a.label.includes(q.trim())), ...pages];
  }, [q, hist.data, navigate, onRun, onToggleTheme]);
  const exec = (i: Item | undefined) => { if (!i) return; onClose(); i.run(); };
  let lastGroup = "";
  return (
    <div className="modal-wrap on" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-label="搜索">
        <input ref={input} value={q} placeholder="代码 / 名称 / 页面 / 操作…" onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
            else if (e.key === "Enter") exec(items[sel]);
            else if (e.key === "Escape") onClose();
          }} />
        <div className="cmdk__list">
          {items.map((it, i) => {
            const head = it.group !== lastGroup ? <div className="cmdk__grp">{it.group}</div> : null;
            lastGroup = it.group;
            return <div key={it.key}>{head}<div className={`cmdk__it${i === sel ? " sel" : ""}`} onMouseEnter={() => setSel(i)} onClick={() => exec(it)}><span>{it.label}</span><span className="muted small">{it.hint}</span></div></div>;
          })}
          {!items.length && <div className="muted" style={{ padding: 20 }}>无匹配</div>}
        </div>
        <div className="cmdk__hint">↑↓ 选择 · Enter 打开 · Esc 关闭</div>
      </div>
    </div>
  );
}
