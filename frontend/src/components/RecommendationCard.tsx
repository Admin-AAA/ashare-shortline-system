import { memo, useState, type MouseEvent } from "react";
import { Target, AlertTriangle, ChevronDown, ChevronUp, Layers3, BrainCircuit, Trash2, ShieldAlert, Database } from "lucide-react";
import type { RecommendationCard, SkillSignal } from "../types";
import ScoreGauge from "./ScoreGauge";
import AgentReviewPanel from "./AgentReviewPanel";
import { scoreColor } from "../utils/display";

type Props = {
  item: RecommendationCard;
  isSelected?: boolean;
  onSelect: (id: string) => void;
  onWatch: (id: string, status: string) => void;
  onDelete: (id: string) => void;
};

export default memo(function EnhancedRecommendationCard({ item, isSelected, onSelect, onWatch, onDelete }: Props) {
  const [expanded, setExpanded] = useState(false);
  const borderColor = scoreColor(item.final_score);
  const execution = item.execution_viability;
  const showExecution = execution?.status && execution.status !== "tradable";
  const executionTitle = [
    execution?.block_reason,
    execution?.details?.open_times != null ? `开板 ${execution.details.open_times} 次` : null,
    execution?.details?.fd_amount_yi != null ? `封单 ${Number(execution.details.fd_amount_yi).toFixed(2)} 亿` : null,
  ].filter(Boolean).join(" · ");
  const degraded = !!item.degraded_reason;
  const degradedTitle = item.degraded_reason
    ? `本次推荐为降级输出：${item.degraded_reason}。仅按规则预审，缺失语义审查，请结合分数组成与风险标签谨慎复核。`
    : "";

  function handleCardClick(event: MouseEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest("button, a, [data-card-interactive='true']")) {
      return;
    }
    onSelect(item.recommendation_id);
  }

  const sectorPenalty = Number(item.feature_snapshot?.sector_diversity_penalty ?? 0);
  const fs = item.feature_snapshot ?? {};
  const missingContext = [
    fs.has_real_capital_flow === false && "资金",
    fs.has_real_valuation === false && "估值",
    fs.has_real_news === false && "情报",
  ].filter(Boolean) as string[];

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
          {degraded && (
            <span
              className="erc-degraded-badge"
              title={degradedTitle}
              role="status"
              aria-label="降级输出"
            >
              <AlertTriangle size={11} />
              降级输出
            </span>
          )}
          {sectorPenalty > 0 && (
            <span
              className="erc-sector-penalty"
              title={`板块去重惩罚 -${sectorPenalty} 分：同行业推荐过多，控制集中度`}
              role="status"
              aria-label="板块去重惩罚"
            >
              <Layers3 size={11} />
              -{sectorPenalty}
            </span>
          )}
          {missingContext.length > 0 && (
            <span
              className="erc-data-missing"
              title={`以下数据为默认值，未接入真实数据源：${missingContext.join("、")}`}
              role="status"
              aria-label="数据缺失"
            >
              <Database size={11} />
              {missingContext.join("·")}
            </span>
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
            {fs.pos20 != null && (
              <span className={`erc-factor erc-pos20 ${Number(fs.pos20) > 95 ? "erc-factor-warn" : ""}`} title="20日内位置（>95 过热）">
                P{Number(fs.pos20).toFixed(0)}
              </span>
            )}
            {fs.dsa_trend_score != null && (
              <span className={`erc-factor erc-trend ${Number(fs.dsa_trend_score) < 18 ? "erc-factor-warn" : ""}`} title="DSA趋势分（<18 不足）">
                T{Number(fs.dsa_trend_score).toFixed(0)}
              </span>
            )}
            <span className="erc-skill-count">
              <BrainCircuit size={12} />
              {item.skill_breakdown?.length ?? 0} Skills
            </span>
            {item.setup_type && <span className="erc-setup">{item.setup_type}</span>}
            {item.market_regime && (
              <span className="erc-market-regime">{item.market_regime}</span>
            )}
            {showExecution && (
              <span className={`erc-execution erc-execution-${execution.status}`} title={executionTitle || execution.label}>
                <ShieldAlert size={12} />
                {execution.label || "执行风险"}
              </span>
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
          {(degraded ? item.risk_tags : item.risk_tags.slice(0, 3)).map((tag: string, i: number) => (
            <span key={i} className="risk-tag">{tag}</span>
          ))}
        </div>
      )}
      {item.info_tags && item.info_tags.length > 0 && (
        <div className="erc-infos">
          {(degraded ? item.info_tags : item.info_tags.slice(0, 2)).map((tag: string, i: number) => (
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
