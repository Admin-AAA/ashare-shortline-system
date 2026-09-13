"""构造"生产同源"ivol_dn20 全市场分位图（不写库），用于 DDL 被阻塞时先行完成生产口径终审。

为什么与未来 daily_kbars.ivol_dn20 列逐格等价：
- 取数查询与 `scripts/backfill_ivol20.py --metric ivol_dn20` 完全一致
  （非北交所、不剔 ST、不做 close/open 正数过滤、不剔 ST 名称）。
- 面板构造复用同一个 `app.services.risk_features.build_ivol_dn20_panel`
  （因此公式/窗口/min_periods 与生产列同一份代码）。
- 分位口径复用 `_load_pct_map(..., positive_only=False)` 的语义：
  按日 `rank(pct=True, method='average')`，只保留 ivol_dn20 非空行。
- 唯一差别是 warmup 起点（本脚本从 2024-06-01 全量起算，回填按 120 自然日 warmup），
  对 20 日滚动窗而言二者在 W1 起点（2025-01-06）之前都早已收敛，取值相同。

输出：output/research/ivol_dn20_prodmap.pkl（{ (date, code): pct }）
"""
from __future__ import annotations
import sys, pickle
from pathlib import Path
import pandas as pd
from sqlalchemy import text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal  # noqa: E402
from app.services.risk_features import build_ivol_dn20_panel, BENCHMARK_TS_CODE  # noqa: E402

OUT = B / "output" / "research"
LOAD_FROM = "2024-06-01"


def main() -> None:
    with SessionLocal() as s:
        kbars = pd.DataFrame(s.execute(text(
            """SELECT code, trade_date, pct_chg FROM daily_kbars
               WHERE trade_date >= :w
                 AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'
               ORDER BY code, trade_date"""), {"w": LOAD_FROM}).mappings().all())
        idx = pd.DataFrame(s.execute(text(
            "SELECT trade_date, pct_chg FROM index_daily WHERE ts_code=:c ORDER BY trade_date"),
            {"c": BENCHMARK_TS_CODE}).mappings().all())
    print(f"kbars rows={len(kbars):,} index rows={len(idx):,}", flush=True)
    panel = build_ivol_dn20_panel(kbars, idx).dropna(subset=["ivol_dn20"])
    print(f"panel rows={len(panel):,} dates={panel.trade_date.nunique()} "
          f"codes={panel.code.nunique()}", flush=True)
    panel["pct"] = panel.groupby("trade_date")["ivol_dn20"].rank(pct=True, method="average")
    amap = {(str(r.trade_date), str(r.code)): float(r.pct) for r in panel.itertuples()}
    with open(OUT / "ivol_dn20_prodmap.pkl", "wb") as fh:
        pickle.dump(amap, fh)
    g = panel.groupby(panel.trade_date.str[:7]).size()
    print("按月末行数:", {k: int(v) for k, v in g.items()}, flush=True)
    print(f"saved {len(amap):,} pairs -> output/research/ivol_dn20_prodmap.pkl", flush=True)


if __name__ == "__main__":
    main()
