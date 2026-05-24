import { memo, useState, type MouseEvent } from "react";
import { Target, AlertTriangle, ChevronDown, ChevronUp, Layers3, BrainCircuit, Trash2 } from "lucide-react";
import type { RecommendationCard, SkillSignal } from "../types";
import ScoreGauge from "./ScoreGauge";
import AgentReviewPanel from "./AgentReviewPanel";

type Props = {
  item: RecommendationCard;
  isSelected?: boolean;
  onSelect: (id: string) => void;
  onWatch: (id: string, status: string) => void;
  onDelete: (id: string) => void;
};

const SCORE_COLORS: Record<string, string> = {
  "80": "#1a6b4a",
  "64": "#2d8a56",
  "50": "#b8860b",
  "0": "#c0392b",
};

function scoreBorderColor(score: number): string {
  if (score >= 80) return SCORE_COLORS["80"];
  if (score >= 64) return SCORE_COLORS["64"];
  if (score >= 50) return SCORE_COLORS["50"];
  return SCORE_COLORS["0"];
}

export default memo(function EnhancedRecommendationCard({ item, isSelected, onSelect, onWatch, onDelete }: Props) {
  const [expanded, setExpanded] = useState(false);
  const borderColor = scoreBorderColor(item.final_score);

  function handleCardClick(event: MouseEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest("button, a, [data-card-interactive='true']")) {
      return;
    }
    onSelect(item.recommendation_id);
  }

  function toggleAgentReview(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    setExpanded((current) => !current);
  }

  return (
    <div
      className={`enhanced-rec-card ${isSelected ? "selected" : ""}`}
      style={{ borderLeft: `3px solid ${borderColor}` }}
      onClick={handleCardClick}
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
          <button className="ghost-btn small" type="button" onClick={() => onWatch(item.recommendation_id, "watching")}>
            <Target size={13} />
          </button>
          <button className="ghost-btn small danger" type="button" onClick={() => onDelete(item.recommendation_id)}>
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      <div className="erc-body">
        <div className="erc-gauge">
          <ScoreGauge score={item.final_score} size={100} />
        </div>
        <div className="erc-info">
          <div className="erc-rationale">{item.rationale || "暂无摘要"}</div>
          <div className="erc-meta-row">
            <span className="erc-price">{(item.recommend_price ?? 0).toFixed(2)}</span>
            {item.stop_loss != null ? <span>止损 {(item.stop_loss ?? 0).toFixed(2)}</span> : null}
            {item.take_profit != null ? <span>止盈 {(item.take_profit ?? 0).toFixed(2)}</span> : null}
            <span className="erc-skill-count">
              <BrainCircuit size={12} />
              {item.skill_breakdown?.length ?? 0} Skills
            </span>
            {item.setup_type && <span className="erc-setup">{item.setup_type}</span>}
            {item.market_regime && (
              <span className="erc-market-regime">{item.market_regime}</span>
            )}
          </div>
          {item.skill_disagreement && item.skill_disagreement.disagreement_level !== "一致" && (
            <div className={`erc-disagreement erc-disagreement-${item.skill_disagreement.disagreement_level}`}>
              技能分歧：{item.skill_disagreement.disagreement_level}
              {item.skill_disagreement.stance_conflict ? " · 多空冲突" : ""}
            </div>
          )}
        </div>
      </div>

      {item.risk_tags && item.risk_tags.length > 0 && (
        <div className="erc-risks">
          <AlertTriangle size={12} />
          {item.risk_tags.slice(0, 3).map((tag: string, i: number) => (
            <span key={i} className="risk-tag">{tag}</span>
          ))}
        </div>
      )}
      {item.info_tags && item.info_tags.length > 0 && (
        <div className="erc-infos">
          {item.info_tags.slice(0, 2).map((tag: string, i: number) => (
            <span key={i} className="info-tag">{tag}</span>
          ))}
        </div>
      )}

      <button
        className="erc-expand-btn"
        type="button"
        aria-expanded={expanded}
        onClick={toggleAgentReview}
      >
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        {expanded ? "收起 Agent 审查" : "查看 Agent 审查详情"}
        <Layers3 size={14} />
      </button>

      {expanded && item.skill_breakdown && (
        <div
          className="erc-agent-reviews"
          data-card-interactive="true"
          onClick={(event) => event.stopPropagation()}
        >
          {item.skill_breakdown.map((signal: SkillSignal) => {
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
});
