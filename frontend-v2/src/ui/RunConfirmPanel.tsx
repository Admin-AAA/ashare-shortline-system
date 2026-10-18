import { useEffect, useMemo, useState } from "react";
import { Loader2, PlayCircle, X } from "lucide-react";
import { useCreateRunMutation, usePresetsQuery, useRunsQuery } from "../api/queries/useRunQueries";
import { useUrlRoute } from "../app/useUrlRoute";
import { SHORT_STRATEGIES, pauseReason } from "./strategyRules";
import { useMarketState } from "./useMarketState";

/** 运行推荐确认面板（POST /api/runs）。文案按 v4 §10。 */
export function RunConfirmPanel({ onClose }: { onClose: () => void }) {
  const presets = usePresetsQuery();
  const runs = useRunsQuery(30);
  const { market, tradeDate } = useMarketState();
  const create = useCreateRunMutation();
  const { navigate } = useUrlRoute();
  const [preset, setPreset] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (!preset && presets.data?.length) setPreset(presets.data.find((p) => p.status === "active")?.preset_id ?? presets.data[0].preset_id);
  }, [presets.data, preset]);
  const todayRuns = useMemo(() => {
    const d = (tradeDate ?? "").replace(/-/g, "");
    const today = new Date();
    const local = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, "0")}${String(today.getDate()).padStart(2, "0")}`;
    return (runs.data ?? []).filter((r) => r.run_id.includes(`run_${local}`) || (d && r.run_id.includes(`run_${d}`))).slice(0, 8);
  }, [runs.data, tradeDate]);
  const sel = presets.data?.find((p) => p.preset_id === preset);
  async function go() {
    setMsg("正在触发…");
    try {
      const r = await create.mutateAsync({ preset_id: preset, run_name: "手动推荐" });
      setMsg(`已触发 ${r.run.run_id}`);
      onClose();
      navigate("/run", { params: { run_id: r.run.run_id } });
    } catch (e) {
      setMsg(`运行失败：${(e as Error).message}`);
    }
  }
  return (
    <div className="modal-wrap on" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label="运行推荐">
        <div className="modal__hd"><h3>运行推荐（手动）</h3><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={16} /></button></div>
        <div className="modal__bd">
          <div className="note">会新增一次运行，今日页改为显示本次结果；17:35 的记录保留在运行记录里；已推荐过的票不会重复出。</div>
          <div className="muted small" style={{ margin: "12px 0 6px" }}>今日已有运行</div>
          {todayRuns.length ? (
            <table className="tbl tbl--mini"><tbody>{todayRuns.map((r) => (
              <tr key={r.run_id}><td className="mono small">{r.run_id}</td><td>{r.preset_name}</td><td className="n">出票 {r.recommended_count}</td><td><span className="tag">{r.status}</span></td></tr>
            ))}</tbody></table>
          ) : <div className="muted small">今日暂无运行</div>}
          <div className="muted small" style={{ margin: "12px 0 6px" }}>运行预设</div>
          <select className="inp" value={preset} onChange={(e) => setPreset(e.target.value)} style={{ width: "100%" }}>
            {(presets.data ?? []).map((p) => <option key={p.preset_id} value={p.preset_id}>{p.name}</option>)}
          </select>
          {sel && <div className="muted small" style={{ marginTop: 6 }}>{sel.description} · 出票线 {sel.score_line}</div>}
          <div className="muted small" style={{ margin: "12px 0 6px" }}>今日策略状态（按规则推算）</div>
          <div className="chips">{SHORT_STRATEGIES.map((s) => { const w = pauseReason(s.id, market); return (
            <span key={s.id} className={`tag ${w ? "tag--warn" : s.status === "观察" ? "" : "tag--brand"}`}>{s.short}{s.status === "观察" ? "（观察）" : ""}{w ? ` · 暂停：${w}` : ""}</span>
          ); })}</div>
          <div className="muted small" style={{ marginTop: 10 }}>风险提示由模型并行生成，仅供参考，不影响出票。</div>
          {msg && <div className="small" style={{ marginTop: 8 }}>{msg}</div>}
        </div>
        <div className="modal__ft">
          <button className="btn btn--ghost" onClick={onClose}>取消</button>
          <button className="btn btn--primary" onClick={go} disabled={!preset || create.isPending}>{create.isPending ? <Loader2 size={14} className="spin" /> : <PlayCircle size={14} />}确认运行</button>
        </div>
      </div>
    </div>
  );
}
