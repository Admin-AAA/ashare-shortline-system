import { useState } from "react";
import { BarChart3, Clock, Database, Layers, Plug } from "lucide-react";
import { ModelRoutesSection } from "../components/ModelRoutesSection";
import { PresetsSection } from "../components/PresetsSection";
import { SchedulerTasksSection } from "../components/SchedulerTasksSection";
import { DataHealthSection } from "./DataHealthPage";
import { SystemStatsSection } from "../components/SystemStatsSection";

type ConfigTab = "scheduler" | "llm" | "presets" | "data-health" | "stats";

export function ConfigPage() {
  const [tab, setTab] = useState<ConfigTab>(() => {
    const q = new URLSearchParams(window.location.search).get("tab");
    return q === "scheduler" || q === "llm" || q === "presets" || q === "stats" ? q : "data-health";
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <div className="sub-tabs" style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--border)" }}>
        <SubTab active={tab === "data-health"} onClick={() => setTab("data-health")}>
          <Database size={14} />
          数据健康
        </SubTab>
        <SubTab active={tab === "stats"} onClick={() => setTab("stats")}>
          <BarChart3 size={14} />
          系统统计
        </SubTab>
        <SubTab active={tab === "scheduler"} onClick={() => setTab("scheduler")}>
          <Clock size={14} />
          调度任务
        </SubTab>
        <SubTab active={tab === "llm"} onClick={() => setTab("llm")}>
          <Plug size={14} />
          LLM 接口
        </SubTab>
        <SubTab active={tab === "presets"} onClick={() => setTab("presets")}>
          <Layers size={14} />
          预设管理
        </SubTab>
      </div>

      {tab === "scheduler" && <SchedulerTasksSection />}
      {tab === "llm" && <ModelRoutesSection />}
      {tab === "presets" && <PresetsSection />}
      {tab === "data-health" && <DataHealthSection />}
      {tab === "stats" && <SystemStatsSection />}
    </div>
  );
}

function SubTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "10px 18px",
        background: "transparent",
        border: "none",
        borderBottom: active ? "2px solid var(--accent, var(--color-up))" : "2px solid transparent",
        color: active ? "var(--text)" : "var(--muted)",
        fontWeight: active ? 650 : 500,
        fontSize: "var(--text-sm)",
        cursor: "pointer",
        transition: "color 160ms ease, border-color 160ms ease",
        marginBottom: -1,
      }}
    >
      {children}
    </button>
  );
}
