import { Flame, TrendingDown, TrendingUp } from "lucide-react";
import type { MarketTemperature } from "../../../types";
import { aShareDownClass, aShareUpClass, businessStatusLabel } from "../../../utils/display";

export default function MarketTemperaturePanel({ market }: { market: MarketTemperature }) {
  return (
    <section className="cockpit-panel market-temperature">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">今日市场温度</div>
          <h2>Market Heat</h2>
        </div>
        <Flame size={20} />
      </div>
      <div className="temperature-core">
        <div className="temperature-score">{market.heat_score.toFixed(1)}</div>
        <div className="temperature-regime">
          <span>{businessStatusLabel(market.regime, "市场状态待确认")}</span>
          <strong>{market.sample_count}</strong>
          <small>样本池</small>
        </div>
      </div>
      <div className="heat-meter" aria-hidden="true">
        <span style={{ width: `${Math.max(0, Math.min(100, market.heat_score))}%` }} />
      </div>
      <div className="metric-grid">
        <span>交易日<strong>{market.trade_date || "-"}</strong></span>
        <span><TrendingUp size={14} />上涨<strong className={aShareUpClass()}>{market.up_count}</strong></span>
        <span><TrendingDown size={14} />下跌<strong className={aShareDownClass()}>{market.down_count}</strong></span>
        <span>涨比<strong>{(market.up_ratio * 100).toFixed(1)}%</strong></span>
        <span>涨停<strong className={aShareUpClass()}>{market.limit_up_count}</strong></span>
        <span>跌停<strong className={aShareDownClass()}>{market.limit_down_count}</strong></span>
      </div>
      <div className="theme-strip">
        {market.main_industries.length ? market.main_industries.map((item) => <span key={item}>{item}</span>) : <span>暂无主线</span>}
      </div>
    </section>
  );
}
