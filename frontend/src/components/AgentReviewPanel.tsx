import { BrainCircuit, ShieldAlert, TrendingUp, Target, AlertTriangle, CheckCircle, BarChart3 } from "lucide-react";

type AgentReview = {
  stance?: string;
  score?: number;
  score_delta?: number;
  confidence?: number;
  supports?: string[];
  risks?: string[];
  summary?: string;
  lenses?: string[];
  runtime?: string;
  market_regime_judgment?: string;
  action?: string;
  position_range?: string;
  entry_style?: string;
  invalidation_conditions?: string[];
  next_day_plan?: string[];
  instruction_digest?: Record<string, unknown>;
  hallucination_flags?: string[];
  hallucination_severity?: string;
};

type Props = {
  review: AgentReview;
  skillName: string;
};

const STANCE_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  strong_support: { label: "强烈支持", color: "#1a6b4a", icon: <TrendingUp size={14} /> },
  support: { label: "支持", color: "#2d8a56", icon: <CheckCircle size={14} /> },
  neutral_support: { label: "中性偏多", color: "#7cb895", icon: <CheckCircle size={14} /> },
  neutral: { label: "中性", color: "#87928b", icon: <BarChart3 size={14} /> },
  oppose: { label: "反对", color: "#c0392b", icon: <AlertTriangle size={14} /> },
  veto: { label: "否决", color: "#a01020", icon: <ShieldAlert size={14} /> },
};

export default function AgentReviewPanel({ review, skillName }: Props) {
  const stance = review.stance ?? "neutral";
  const config = STANCE_CONFIG[stance] ?? STANCE_CONFIG.neutral;
  const score = review.score ?? 50;
  const confidence = (review.confidence ?? 0.5) * 100;
  const scoreColor = score >= 72 ? "#1a6b4a" : score >= 50 ? "#b8860b" : "#c0392b";
  const planCards = [
    review.action ? { label: "动作", value: review.action } : null,
    review.position_range ? { label: "仓位", value: review.position_range } : null,
    review.entry_style ? { label: "入场", value: review.entry_style } : null,
    review.market_regime_judgment ? { label: "市场", value: review.market_regime_judgment } : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item?.value));

  return (
    <div className="agent-review-panel">
      <div className="arp-header">
        <div className="arp-skill-name">
          <BrainCircuit size={15} />
          <span>{skillName}</span>
        </div>
        <div className="arp-score-row">
          <div className="arp-score" style={{ color: scoreColor }}>
            <span className="arp-score-num">{score}</span>
            <span className="arp-score-label">/100</span>
          </div>
          <div className="arp-confidence">
            <Target size={12} />
            <span>{confidence.toFixed(0)}% 置信</span>
          </div>
        </div>
      </div>

      <div className="arp-stance" style={{ borderColor: config.color, background: `${config.color}0a` }}>
        <span className="arp-stance-icon" style={{ color: config.color }}>{config.icon}</span>
        <span style={{ color: config.color, fontWeight: 600 }}>{config.label}</span>
      </div>

      {review.summary && (
        <div className="arp-summary">{review.summary}</div>
      )}

      {planCards.length > 0 && (
        <div className="arp-plan-grid">
          {planCards.map((item) => (
            <div key={item.label} className="arp-plan-card">
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      )}

      {(review.supports && review.supports.length > 0) && (
        <div className="arp-list supports">
          <div className="arp-list-title">
            <TrendingUp size={13} />
            <span>支持证据</span>
          </div>
          {review.supports.slice(0, 4).map((item, i) => (
            <div key={i} className="arp-list-item">{item}</div>
          ))}
        </div>
      )}

      {(review.risks && review.risks.length > 0) && (
        <div className="arp-list risks">
          <div className="arp-list-title">
            <AlertTriangle size={13} />
            <span>风险提示</span>
          </div>
          {review.risks.slice(0, 4).map((item, i) => (
            <div key={i} className="arp-list-item">{item}</div>
          ))}
        </div>
      )}

      {(review.invalidation_conditions && review.invalidation_conditions.length > 0) && (
        <div className="arp-list invalidation">
          <div className="arp-list-title">
            <ShieldAlert size={13} />
            <span>失效条件</span>
          </div>
          {review.invalidation_conditions.slice(0, 3).map((item, i) => (
            <div key={i} className="arp-list-item">{item}</div>
          ))}
        </div>
      )}

      {(review.next_day_plan && review.next_day_plan.length > 0) && (
        <div className="arp-list next-plan">
          <div className="arp-list-title">
            <Target size={13} />
            <span>次日计划</span>
          </div>
          {review.next_day_plan.slice(0, 3).map((item, i) => (
            <div key={i} className="arp-list-item">{item}</div>
          ))}
        </div>
      )}

      {review.hallucination_flags && review.hallucination_flags.length > 0 && (
        <div className="arp-hallucination">
          <ShieldAlert size={13} />
          <span>可追溯性问题 ({review.hallucination_severity ?? "low"})</span>
        </div>
      )}

      <div className="arp-meta">
        <span>Runtime: {String(review.runtime ?? "agent")}</span>
        {review.instruction_digest?.body_hash ? (
          <span>Hash: {String(review.instruction_digest.body_hash).slice(0, 8)}</span>
        ) : null}
      </div>
    </div>
  );
}
