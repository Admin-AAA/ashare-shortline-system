import { SHORT_STRATEGIES, pauseReason } from "./strategyRules";
import { useMarketState } from "./useMarketState";

/** 顶部市场状态条：市场阶段 / 上涨家数占比 / 涨停−跌停 / 今日暂停策略（按规则推算） */
export function MarketBar() {
  const { market, tradeDate, hotTheme } = useMarketState();
  if (!market) return <div className="mkt"><span className="muted">市场状态加载中…</span></div>;
  const paused = SHORT_STRATEGIES.map((s) => ({ s, why: pauseReason(s.id, market) })).filter((x) => x.why);
  const net = market.limitUp - market.limitDown;
  return (
    <div className="mkt" role="status">
      <span><b>市场阶段</b><span className="tag tag--warn">{market.regime}</span></span>
      <span><b>上涨家数占比</b><span className="num">{market.upRatio == null ? "—" : `${(market.upRatio * 100).toFixed(0)}%`}</span></span>
      <span>
        <b>涨停−跌停</b>
        <span className="num up">{market.limitUp}</span>−<span className="num down">{market.limitDown}</span> = <span className="num">{net}</span>
      </span>
      {hotTheme && <span><b>主线</b>{hotTheme}</span>}
      <span className="sep" />
      <span title="后端未直接提供暂停字段，按领域规则基于市场状态推算">
        <b>今日暂停</b>
        {paused.length ? paused.map((x) => <span key={x.s.id} className="tag tag--warn" style={{ marginRight: 4 }}>{x.s.short} · {x.why}</span>) : <span className="muted">无</span>}
        <span className="muted small"> (按规则推算)</span>
      </span>
      <span style={{ marginLeft: "auto" }} className="muted">数据日 <span className="num">{tradeDate}</span></span>
    </div>
  );
}
