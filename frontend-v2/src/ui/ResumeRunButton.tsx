import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useUrlRoute } from "../app/useUrlRoute";

/** 失败/中断运行行上的「续跑」：先读详情拿 can_resume / resume_block_reason，可续跑才调用 resumeRun。 */
export function ResumeRunButton({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const { navigate } = useUrlRoute();
  const [state, setState] = useState<"idle" | "checking" | "running" | "blocked" | "done">("idle");
  const [msg, setMsg] = useState("");
  async function go(e: React.MouseEvent) {
    e.stopPropagation();
    setState("checking");
    try {
      const d = await api.runDetail(runId);
      if (!d.can_resume) { setState("blocked"); setMsg(d.resume_block_reason || "该运行不可安全续跑，请新建运行"); return; }
      if (!window.confirm(`续跑 ${runId}？将从中断阶段继续，不重放历史。`)) { setState("idle"); return; }
      setState("running");
      await api.resumeRun(runId);
      setState("done");
      void qc.invalidateQueries();
      navigate("/run", { params: { run_id: runId } });
    } catch (err) { setState("blocked"); setMsg((err as Error).message || "续跑失败"); }
  }
  if (state === "blocked") return <span className="tag tag--warn" title={msg} onClick={(e) => e.stopPropagation()} style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>不可续跑：{msg}</span>;
  return (
    <button className="btn btn--ghost" style={{ height: 26, padding: "0 10px", fontSize: 12 }} disabled={state === "checking" || state === "running"} onClick={go}>
      {state === "checking" ? "检查中…" : state === "running" ? "续跑中…" : state === "done" ? "已续跑" : "续跑"}
    </button>
  );
}
