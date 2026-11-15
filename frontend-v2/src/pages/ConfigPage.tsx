import { useState } from "react";
import { BarChart3, Clock, Database, Plug, Server } from "lucide-react";
import { ModelRoutesSection } from "../components/ModelRoutesSection";
import { SchedulerTasksSection } from "../components/SchedulerTasksSection";
import { DataSourcesSection } from "../components/DataSourcesSection";
import { DataHealthSection } from "./DataHealthPage";
import { SystemStatsSection } from "../components/SystemStatsSection";

type ConfigTab = "scheduler" | "llm" | "data-health" | "data-sources" | "stats";

export function ConfigPage() {
  const [tab, setTab] = useState<ConfigTab>(() => {
    const q = new URLSearchParams(window.location.search).get("tab");
    return q === "scheduler" || q === "llm" || q === "stats" || q === "data-sources" ? q : "data-health";
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <div className="sub-tabs" style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--border)" }}>
        <SubTab active={tab === "data-health"} onClick={() => setTab("data-health")}>
          <Database size={14} />
          数据健康
        </SubTab>
        <SubTab active={tab === "data-sources"} onClick={() => setTab("data-sources")}>
          <Server size={14} />
          数据源
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
      </div>

      {tab === "scheduler" && <SchedulerTasksSection />}
      {tab === "llm" && <ModelRoutesSection />}
      {tab === "data-health" && <DataHealthSection />}
      {tab === "data-sources" && <DataSourcesSection />}
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
