import { memo } from "react";
import type { MarketSnapshot } from "../types";
import { aShareDownClass, aShareUpClass, businessStatusLabel } from "../utils/display";

type Props = {
  snapshot: MarketSnapshot;
};

export default memo(function MarketSnapshotPanel({ snapshot }: Props) {
  const breadth = snapshot.breadth || {};
  return (
    <div className="market-snapshot-panel">
      <div className="market-snapshot-grid">
        <MiniStatCell label="数据" value={businessStatusLabel(breadth.data_source, "状态待确认")} />
        <MiniStatCell label="报价源" value={String(breadth.quote_source ?? "-")} />
        <MiniStatCell label="上涨" value={snapshot.up_count} valueClass={aShareUpClass()} />
        <MiniStatCell label="下跌" value={snapshot.down_count} valueClass={aShareDownClass()} />
        <MiniStatCell label="涨停" value={snapshot.limit_up_count} valueClass={aShareUpClass()} />
        <MiniStatCell label="跌停" value={snapshot.limit_down_count} valueClass={aShareDownClass()} />
        <MiniStatCell label="主线" value={snapshot.main_industries.slice(0, 2).join(" / ")} />
        <div className="summary-block wide">
          <span>市场结论</span>
          <strong>{String(breadth.regime ?? "未知")}</strong>
        </div>
      </div>
    </div>
  );
});

function MiniStatCell({ label, value, valueClass }: { label: string; value: string | number; valueClass?: string }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong className={valueClass}>{value}</strong>
    </div>
  );
}
