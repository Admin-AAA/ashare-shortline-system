import { TrendingUp, Target, AlertTriangle, ChevronDown, ChevronUp, Layers3, BrainCircuit } from "lucide-react";
import { useState } from "react";
import type { RecommendationCard, SkillSignal } from "../types";
import ScoreGauge from "./ScoreGauge";
import AgentReviewPanel from "./AgentReviewPanel";

type Props = {
  item: RecommendationCard;
  isSelected?: boolean;
  onSelect: (id: string) => void;
  onWatch: (id: string, status: string) => void;
};

const BUCKET_COLORS: Record<string, string> = {
  "容量核心": "#1a6b4a",
  "高弹观察": "#2d8a56",
  "证据不足": "#b8860b",
  "明确回避": "#c0392b",
};

export default function EnhancedRecommendationCard({ item, isSelected, onSelect, onWatch }: Props) {
  const [expanded, setExpanded] = useState(false);
  const bucketColor = BUCKET_COLORS[item.bucket] ?? "#87928b";

  return (
    <div
      className={`enhanced-rec-card ${isSelected ? "selected" : ""}`}
      style={{ borderLeft: `3px solid ${bucketColor}` }}
      onClick={() => onSelect(item.recommendation_id)}
    >
      <div className="erc-header">
        <div className="erc-code-name">
          <span className="erc-code">{item.code}</span>
          <span className="erc-name">{item.name}</span>
          {item.industry && item.industry !== "未分类" && (
            <span className="erc-industry">{item.industry}</span>
          )}
        </div>
        <div className="erc-actions" onClick={(e) => e.stopPropagation()}>
          <button className="ghost-btn small" onClick={() => onWatch(item.recommendation_id, "watching")}>
            <Target size={13} />
          </button>
        </div>
      </div>

      <div className="erc-body">
        <div className="erc-gauge">
          <ScoreGauge score={item.final_score} size={100} />
          <div className="erc-bucket" style={{ color: bucketColor }}>
            {item.bucket}
          </div>
        </div>
        <div className="erc-info">
          <div className="erc-rationale">{item.rationale || "暂无摘要"}</div>
          <div className="erc-meta-row">
            <span className="erc-price">{(item.recommend_price ?? 0).toFixed(2)}</span>
            <span className="erc-skill-count">
              <BrainCircuit size={12} />
              {item.skill_breakdown?.length ?? 0} Skills
            </span>
            {item.setup_type && <span className="erc-setup">{item.setup_type}</span>}
          </div>
        </div>
      </div>

      {item.risk_tags && item.risk_tags.length > 0 && (
        <div className="erc-risks">
          <AlertTriangle size={12} />
          {item.risk_tags.slice(0, 3).map((tag, i) => (
            <span key={i} className="risk-tag">{tag}</span>
          ))}
        </div>
      )}

      <button
        className="erc-expand-btn"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded(!expanded);
        }}
      >
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        {expanded ? "收起 Agent 审查" : "查看 Agent 审查详情"}
        <Layers3 size={14} />
      </button>

      {expanded && item.skill_breakdown && (
        <div className="erc-agent-reviews">
          {item.skill_breakdown.map((signal) => {
            const review = signal.agent_skill_review;
            if (!review) {
              return (
                <div key={signal.version_id} className="erc-no-agent-review">
                  <span>{signal.strategy_name}</span>
                  <span>无 Agent 审查（规则评分 {signal.score?.toFixed(1)}）</span>
                </div>
              );
            }
            return (
              <AgentReviewPanel
                key={signal.version_id}
                review={review}
                skillName={signal.strategy_name ?? "未知 Skill"}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
