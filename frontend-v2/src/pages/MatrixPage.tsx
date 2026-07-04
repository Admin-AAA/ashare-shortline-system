import { useState } from "react";
import { LayoutGrid, PlayCircle } from "lucide-react";
import { api } from "../api/client";
import { EmptyState } from "../components/StateViews";

/**
 * 矩阵页 —— 股票矩阵分析的入口与状态
 * 矩阵批量回测通过 POST /api/runs/matrix 触发，status 轮询。
 * 详细矩阵数据在 /stock-matrix 子路由（待后续接入）。
 */
export function MatrixPage() {
  const [starting, setStarting] = useState(false);
  const [status, setStatus] = useState<string>("");

  const startMatrix = () => {
    setStarting(true);
    setStatus("");
    api
      .startMatrixRun({})
      .then((res) => {
        setStatus(res.started ? "矩阵回测已启动" : "已有任务运行中");
      })
      .catch((err) => {
        setStatus(err.message || "启动失败");
      })
      .finally(() => setStarting(false));
  };

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">矩阵</span>
        <h2 className="panel__title">股票矩阵</h2>
      </div>

      <EmptyState
        icon={<LayoutGrid size={32} />}
        title="矩阵批量回测"
        reason="对多只股票批量跑回测，验证策略覆盖度"
        action={
          <button
            onClick={startMatrix}
            disabled={starting}
            className="btn btn--primary"
          >
            <PlayCircle size={16} />
            {starting ? "启动中…" : "启动矩阵回测"}
          </button>
        }
      />

      {status && (
        <div
          style={{
            marginTop: "var(--space-3)",
            padding: "var(--space-3)",
            background: "var(--surface-soft)",
            borderRadius: "var(--radius-md)",
            fontSize: "var(--text-sm)",
            color: "var(--ink-secondary)",
          }}
        >
          {status}
        </div>
      )}
    </div>
  );
}
