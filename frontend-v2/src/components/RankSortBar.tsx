import "../styles/rank.css";
import { SORT_KEYS, formatRankScore, type RankFields, type SortState } from "../utils/strategyRank";

export const RANK_DISCLAIMER = "综合分基于回测，偏乐观，不代表未来表现；只在同一周期内比较，中 / 长 / 短线之间不可比。";

/** 排序栏：综合 / 收益 / 胜率 / 回撤 / 近期 + 升降序；默认综合降序。 */
export function RankSortBar({ sort, onChange }: { sort: SortState; onChange: (s: SortState) => void }) {
  const cur = SORT_KEYS.find((k) => k.key === sort.key);
  return (
    <div className="rk-bar" data-testid="rank-sort-bar">
      <span className="rk-bar__label">排序</span>
      <div className="seg" role="group" aria-label="策略排序">
        {SORT_KEYS.map((k) => (
          <button key={k.key} className={sort.key === k.key ? "on" : ""} title={k.hint}
            onClick={() => onChange({ key: k.key, dir: sort.dir })}>{k.label}</button>
        ))}
      </div>
      <button className="rk-bar__dir" onClick={() => onChange({ key: sort.key, dir: sort.dir === "desc" ? "asc" : "desc" })}
        title={sort.dir === "desc" ? "当前：降序（好的在前），点击切换为升序" : "当前：升序，点击切换为降序"}>
        {sort.dir === "desc" ? "降序 ↓" : "升序 ↑"}
      </button>
      <span className="rk-bar__tip">{RANK_DISCLAIMER}{cur ? `（${cur.label}：${cur.hint}）` : ""}</span>
    </div>
  );
}

/** 卡片 / 表格里的综合分与沉底原因标签。 */
export function RankBadge({ r }: { r: RankFields | undefined | null }) {
  if (!r) return null;
  const labels = r.rank_flag_labels ?? r.rank_flags ?? [];
  return (
    <span className="rk-badge" data-testid="rank-badge">
      {r.rank_score != null && <span className="rk-score" title="综合分（周期内百分位加权，0~100）">综合分 {formatRankScore(r.rank_score)}{r.rank ? ` · #${r.rank}` : ""}</span>}
      {labels.map((l) => <span key={l} className="rk-flag">{l}（沉底）</span>)}
    </span>
  );
}
