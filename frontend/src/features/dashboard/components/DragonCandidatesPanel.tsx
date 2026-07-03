import { ArrowUpRight, BadgeAlert, Database } from "lucide-react";
import type { CockpitRecommendation } from "../../../types";
import { riskLevelLabel } from "../../../utils/display";

type EmptyState = {
  title: string;
  reason: string;
  detail: string;
};

export default function DragonCandidatesPanel({
  items,
  emptyState,
}: {
  items: CockpitRecommendation[];
  emptyState?: EmptyState | null;
}) {
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
                <span><BadgeAlert size={12} />{riskLevelLabel(item.risk_level)}</span>
                <span title={dataSourceTitle(item.data_quality.quote_source_count)}><Database size={12} />{dataSourceText(item.data_quality.quote_source_count)}</span>
              </div>
            </div>
          </article>
        )) : (
          <div className="candidate-empty">
            <strong>{emptyState?.title ?? "当前没有达标龙头候选"}</strong>
            <span>{emptyState?.reason ?? "缺少运行归因，不能直接视为市场无机会。"}</span>
            <p>{emptyState?.detail ?? "请检查最近一次运行状态、数据池门禁和筛选条件。"}</p>
            <button
              className="btn primary candidate-empty-action"
              type="button"
              onClick={() => window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section: "run" } }))}
            >
              去推荐运行
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

function dataSourceText(count: number) {
  return count > 0 ? `${count} 源` : "来源待标注";
}

function dataSourceTitle(count: number) {
  return count > 0 ? `当前候选记录包含 ${count} 个报价源` : "当前候选记录未携带单票数据源明细";
}
