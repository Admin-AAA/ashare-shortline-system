import type { MarketSnapshot } from "../types";

type Props = {
  snapshot: MarketSnapshot;
};

export default function MarketSnapshotPanel({ snapshot }: Props) {
  const breadth = snapshot.breadth || {};
  return (
    <div className="market-snapshot-panel">
      <div className="market-snapshot-grid">
        <MiniStatCell label="数据" value={String(breadth.data_source ?? "unknown")} />
        <MiniStatCell label="报价源" value={String(breadth.quote_source ?? "-")} />
        <MiniStatCell label="上涨" value={snapshot.up_count} />
        <MiniStatCell label="下跌" value={snapshot.down_count} />
        <MiniStatCell label="涨停" value={snapshot.limit_up_count} />
        <MiniStatCell label="跌停" value={snapshot.limit_down_count} />
        <MiniStatCell label="主线" value={snapshot.main_industries.slice(0, 2).join(" / ")} />
        <div className="summary-block wide">
          <span>市场结论</span>
          <strong>{String(breadth.regime ?? "未知")}</strong>
        </div>
      </div>
    </div>
  );
}

function MiniStatCell({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
