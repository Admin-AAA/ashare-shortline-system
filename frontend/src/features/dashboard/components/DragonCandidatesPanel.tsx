import { ArrowUpRight, BadgeAlert, Database } from "lucide-react";
import type { CockpitRecommendation } from "../../../types";

function riskText(level: CockpitRecommendation["risk_level"]) {
  const labels = { low: "低风险", medium: "中风险", high: "高风险", unknown: "未知" };
  return labels[level];
}

export default function DragonCandidatesPanel({ items }: { items: CockpitRecommendation[] }) {
  return (
    <section className="cockpit-panel dragon-candidates">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">龙头候选</div>
          <h2>强度排序</h2>
        </div>
        <span className="count-badge">{items.length} 只</span>
      </div>
      <div className="candidate-list">
        {items.length ? items.map((item, index) => (
          <article key={item.recommendation_id} className={`candidate-card risk-${item.risk_level}`}>
            <div className="candidate-rank">{String(index + 1).padStart(2, "0")}</div>
            <div className="candidate-body">
              <div className="candidate-main">
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.code} · {item.industry || "未分类"}</span>
                </div>
                <div className="score-pill">{item.final_score.toFixed(1)}</div>
              </div>
              <p>{item.rationale || "暂无解释"}</p>
              <div className="candidate-meta">
                <span><ArrowUpRight size={12} />{item.setup_type || "未识别买点"}</span>
                <span>Gap {item.score_gap == null ? "-" : item.score_gap.toFixed(1)}</span>
                <span>{item.consensus_label}</span>
                <span><BadgeAlert size={12} />{riskText(item.risk_level)}</span>
                <span><Database size={12} />{item.data_quality.quote_source_count} 源</span>
              </div>
            </div>
          </article>
        )) : <div className="empty-state">暂无推荐结果</div>}
      </div>
    </section>
  );
}
