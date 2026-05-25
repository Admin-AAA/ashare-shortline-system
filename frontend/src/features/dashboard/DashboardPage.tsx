import DataHealthPanel from "./components/DataHealthPanel";
import DragonCandidatesPanel from "./components/DragonCandidatesPanel";
import MarketTemperaturePanel from "./components/MarketTemperaturePanel";
import RiskRadarPanel from "./components/RiskRadarPanel";
import RunStatusPanel from "./components/RunStatusPanel";
import ValidationPanel from "./components/ValidationPanel";
import { useDashboardViewModel } from "./hooks/useDashboardViewModel";

export default function DashboardPage({ compactMode }: { compactMode?: string }) {
  const { data, loading, error, refresh } = useDashboardViewModel();

  if (loading) return <div className="cockpit-panel loading-state">正在加载驾驶舱数据</div>;
  if (error) {
    return (
      <div className="cockpit-panel error-state">
        <strong>驾驶舱加载失败</strong>
        <span>{error}</span>
        <button type="button" onClick={refresh}>重试</button>
      </div>
    );
  }
  if (!data) return <div className="cockpit-panel">暂无驾驶舱数据</div>;

  return (
    <div className={`dashboard-grid ${compactMode ? "compact" : ""}`}>
      <MarketTemperaturePanel market={data.market} />
      <DragonCandidatesPanel items={data.recommendations} />
      <div className="side-stack">
        <RiskRadarPanel radar={data.risk_radar} />
        <RunStatusPanel run={data.current_run} />
        <DataHealthPanel dataHealth={data.data_health} taskHealth={data.task_health} />
        <ValidationPanel validation={data.validation} />
      </div>
    </div>
  );
}
