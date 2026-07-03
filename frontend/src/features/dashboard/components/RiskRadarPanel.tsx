import { Radar } from "lucide-react";
import type { RiskRadar } from "../../../types";

export default function RiskRadarPanel({ radar }: { radar: RiskRadar }) {
  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">风险雷达</div>
          <h2>风险雷达</h2>
        </div>
        <Radar size={19} />
      </div>
      <div className="risk-grid">
        <span><strong className="negative">{radar.high}</strong>高风险</span>
        <span><strong>{radar.medium}</strong>中风险</span>
        <span><strong>{radar.info}</strong>信息提示</span>
      </div>
      <div className="tag-cloud">
        {radar.tags.length ? radar.tags.map((tag) => <span key={tag}>{tag}</span>) : <span>暂无显著风险</span>}
      </div>
    </section>
  );
}
