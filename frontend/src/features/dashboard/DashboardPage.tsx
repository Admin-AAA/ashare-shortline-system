import { useEffect, useState } from "react";
import BacktestPanel from "../../components/BacktestPanel";
import BacktestEvaluationPanel from "./components/BacktestEvaluationPanel";
import DataHealthDetailPanel from "./components/DataHealthDetailPanel";
import DataHealthPanel from "./components/DataHealthPanel";
import DragonCandidatesPanel from "./components/DragonCandidatesPanel";
import MarketTemperaturePanel from "./components/MarketTemperaturePanel";
import MetricsPanel from "./components/MetricsPanel";
import RiskRadarPanel from "./components/RiskRadarPanel";
import RunStatusPanel from "./components/RunStatusPanel";
import TemporalConsistencyPanel from "./components/TemporalConsistencyPanel";
import ValidationPanel from "./components/ValidationPanel";
import { useDashboardViewModel } from "./hooks/useDashboardViewModel";
import { api } from "../../api/client";
import type { CockpitSection } from "../../app/nav";
import type { CockpitRecommendation, DashboardOverview, MetricsOverview } from "../../types";

type DashboardSection = Exclude<CockpitSection, "control">;

export default function DashboardPage({ section }: { section: DashboardSection }) {
  const { data, loading, error, refresh } = useDashboardViewModel();
  const [metrics, setMetrics] = useState<MetricsOverview | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricsError, setMetricsError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setMetricsLoading(true);
    setMetricsError("");
    api.metricsOverview("formal").then((m) => {
      if (!cancelled) setMetrics(m);
    }).catch((err) => {
      if (!cancelled) setMetricsError(err.message);
    }).finally(() => {
      if (!cancelled) setMetricsLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

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

  if (section === "run") return <RunWorkspace data={data} />;
  if (section === "pool") return <CandidateWorkspace data={data} />;
  if (section === "data") return <DataWorkspace data={data} />;
  if (section === "history") return <ReviewWorkspace data={data} />;
  if (section === "validation") return <ValidationWorkspace data={data} />;

  return (
    <div className="dashboard-grid">
      <MarketTemperaturePanel market={data.market} />
      <MetricsPanel metrics={metrics ?? ({} as MetricsOverview)} loading={metricsLoading} />
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

function RunWorkspace({ data }: { data: DashboardOverview }) {
  const stages = [
    ["prepare", "数据池准备", "本地池、行情源和交易日对齐"],
    ["candidate", "候选过滤", "流动性、主线、风险门槛"],
    ["skill_review", "Skill 审查", "规则分 + Agent 解释"],
    ["persist", "聚合落库", "分桶、排序、复盘闭环"],
  ];
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>推荐运行</span>
          <h2>运行编排</h2>
          <p>围绕当前 run 的阶段、心跳、可恢复性和候选产出建立运行视角。</p>
        </div>
        <strong>{data.current_run.status}</strong>
      </section>
      <div className="focus-grid">
        <RunStatusPanel run={data.current_run} />
        <section className="cockpit-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Pipeline</div>
              <h2>阶段轨迹</h2>
            </div>
          </div>
          <div className="timeline-list">
            {stages.map(([phase, label, note]) => (
              <span key={phase} className={data.current_run.phase === phase ? "active" : ""}>
                <strong>{label}</strong>
                <small>{note}</small>
              </span>
            ))}
          </div>
        </section>
      </div>
      <DragonCandidatesPanel items={data.recommendations.slice(0, 6)} />
    </div>
  );
}

function CandidateWorkspace({ data }: { data: DashboardOverview }) {
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>候选解释</span>
          <h2>个股解释卡</h2>
          <p>每个候选都带强度、原因、风险、共识与数据质量，优先服务短线决策。</p>
        </div>
        <strong>{data.recommendations.length} 只</strong>
      </section>
      <DragonCandidatesPanel items={data.recommendations} />
      <div className="explain-grid">
        {data.recommendations.map((item) => <ExplanationCard key={item.recommendation_id} item={item} />)}
      </div>
    </div>
  );
}

function DataWorkspace({ data }: { data: DashboardOverview }) {
  const messages = stringList(data.data_health.messages);
  const examples = stringList(data.data_health.examples);
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>数据健康</span>
          <h2>数据池与调度健康度</h2>
          <p>拆开本地池覆盖、候选可运行性、调度状态和缺口样例，避免运行前盲扫。</p>
        </div>
        <strong>{textValue(data.data_health.status, "unknown")}</strong>
      </section>
      <div className="focus-grid">
        <DataHealthPanel dataHealth={data.data_health} taskHealth={data.task_health} />
        <section className="cockpit-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Coverage</div>
              <h2>池覆盖细节</h2>
            </div>
          </div>
          <div className="metric-grid">
            <span>活跃报价<strong>{textValue(data.data_health.active_quote_count)}</strong></span>
            <span>日K覆盖<strong>{textValue(data.data_health.bar_coverage_pct)}%</strong></span>
            <span>合格日K<strong>{textValue(data.data_health.eligible_bar_coverage_pct)}%</strong></span>
            <span>本地推荐<strong>{String(data.data_health.can_run_local_recommendation ?? false) === "true" ? "READY" : "CHECK"}</strong></span>
          </div>
        </section>
      </div>
      <div className="focus-grid">
        <DataHealthDetailPanel />
        <TemporalConsistencyPanel />
      </div>
      <StatusStream title="数据池消息" items={messages} empty="暂无数据池消息" />
      <StatusStream title="缺口样例" items={examples} empty="暂无缺口样例" />
    </div>
  );
}

function ReviewWorkspace({ data }: { data: DashboardOverview }) {
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>复盘结果</span>
          <h2>结果反馈闭环</h2>
          <p>从当前推荐出发，聚焦观察状态、复盘入口和后续结果标注，保持策略可学习。</p>
        </div>
        <strong>{data.market.trade_date || "-"}</strong>
      </section>
      <div className="review-list">
        {data.recommendations.length ? data.recommendations.map((item) => (
          <article key={item.recommendation_id} className="cockpit-panel review-row">
            <div>
              <strong>{item.name}</strong>
              <span>{item.code} · {item.setup_type || "未识别买点"}</span>
            </div>
            <div>
              <span>观察状态</span>
              <strong>{item.watch_status}</strong>
            </div>
            <div>
              <span>推荐状态</span>
              <strong>{item.status}</strong>
            </div>
            <div>
              <span>强度</span>
              <strong>{item.final_score.toFixed(1)}</strong>
            </div>
          </article>
        )) : <div className="cockpit-panel empty-state">暂无可复盘推荐</div>}
      </div>
    </div>
  );
}

function ValidationWorkspace({ data }: { data: DashboardOverview }) {
  const alerts = data.validation.alerts as { high?: number; medium?: number; low?: number; messages?: string[] } | undefined;
  return (
    <div className="workspace-grid">
      <section className="section-hero">
        <div>
          <span>验收安全</span>
          <h2>安全验证门禁</h2>
          <p>集中呈现 dry-run、数据池 readiness 和运行风险，防止推荐链路带病上线。</p>
        </div>
        <strong>{textValue(data.validation.last_status, "unknown")}</strong>
      </section>
      <div className="focus-grid">
        <ValidationPanel validation={data.validation} />
        <section className="cockpit-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Gate</div>
              <h2>门禁摘要</h2>
            </div>
          </div>
          <div className="metric-grid">
            <span>高风险<strong className="negative">{alerts?.high ?? 0}</strong></span>
            <span>中风险<strong>{alerts?.medium ?? 0}</strong></span>
            <span>低风险<strong>{alerts?.low ?? 0}</strong></span>
            <span>运行状态<strong>{data.current_run.status}</strong></span>
          </div>
        </section>
      </div>
      <StatusStream title="验证消息" items={alerts?.messages ?? []} empty="暂无验证消息" />
      <TemporalConsistencyPanel />
      <BacktestPanel />
      <BacktestEvaluationPanel />
    </div>
  );
}

function ExplanationCard({ item }: { item: CockpitRecommendation }) {
  return (
    <article className={`cockpit-panel explanation-card risk-${item.risk_level}`}>
      <div className="candidate-main">
        <div>
          <strong>{item.name}</strong>
          <span>{item.code} · {item.industry || "未分类"}</span>
        </div>
        <div className="score-pill">{item.final_score.toFixed(1)}</div>
      </div>
      <p>{item.rationale || "暂无解释"}</p>
      <div className="mini-table">
        <span>风险<strong>{item.risk_level}</strong></span>
        <span>共识<strong>{item.consensus_label}</strong></span>
        <span>报价源<strong>{item.data_quality.quote_source_count}</strong></span>
        <span>日K<strong>{item.data_quality.kline_status}</strong></span>
      </div>
    </article>
  );
}

function StatusStream({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Status</div>
          <h2>{title}</h2>
        </div>
      </div>
      <div className="alert-stream">
        {items.length ? items.slice(0, 8).map((item) => <span key={item}>{item}</span>) : <span>{empty}</span>}
      </div>
    </section>
  );
}

function textValue(value: unknown, fallback = "0") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item));
}
