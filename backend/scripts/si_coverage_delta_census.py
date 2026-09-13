"""增量覆盖普查：对照 L-082 全表普查结论，找出覆盖状态发生变化的表（只读）。

L-082 (2026-09 初) 结论：
- 冻结>1000 已穷尽: daily_kbars daily_basic stk_limits limit_up_board dragon_tiger
  margin_detail auction_data minute_kbars index_daily
- 覆盖即死: capital_flow concept_sector(_stock) news_announcement llm_review_cache
  stock_quotes recommendation(s) hk_holdings hsgt_* institutional_surveys
  broker_recommends pledge_stats repurchases share_floats stock_fina_indicator
  holder_numbers holder_trades block_trades
本轮问题：这些死表是否已被 backfill 解锁？是否有 L-082 之后新增的表？
"""
from __future__ import annotations
import sys
from pathlib import Path
from sqlalchemy import create_engine, text

B = Path(__file__).resolve().parents[1]
DATE_CAND = ["trade_date", "announce_date", "ann_date", "date", "datetime", "end_date",
             "surv_date", "pub_date", "news_date", "day", "dt", "time",
             "created_at", "updated_at", "recommend_date", "hold_date"]
WINS = [("冻结", "2025-01-15", "2026-01-16"), ("前瞻", "2026-02-09", "2026-08-05"),
        ("run3", "2026-08-07", "2026-09-02")]


def engine():
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in (B / ".env").read_text().splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    return create_engine(url)


def main():
    e = engine()
    with e.connect() as c:
        tabs = [x[0] for x in c.execute(text("SHOW TABLES")).all()]
        print(f"共 {len(tabs)} 表")
        for t in sorted(tabs):
            cols = [x[0] for x in c.execute(text(f"SHOW COLUMNS FROM `{t}`")).all()]
            dc = next((d for d in DATE_CAND if d in cols), None)
            if not dc:
                print(f"  {t:34s} (无日期列, cols={','.join(cols[:6])}...)")
                continue
            try:
                cnt = [c.execute(text(f"SELECT COUNT(*) FROM `{t}` WHERE `{dc}` BETWEEN :a AND :b"),
                                 {"a": a, "b": b}).scalar() for _, a, b in WINS]
                mn, mx = c.execute(text(f"SELECT MIN(`{dc}`), MAX(`{dc}`) FROM `{t}`")).fetchone()
            except Exception as ex:
                print(f"  {t:34s} ERR {ex}")
                continue
            flag = "  <==冻结有数据" if cnt[0] > 1000 else ("  <==冻结变化?" )
            print(f"  {t:34s} {dc:14s} 冻结{cnt[0]:>9} 前瞻{cnt[1]:>9} run3{cnt[2]:>8}  范围[{mn} ~ {mx}]{flag if cnt[0]>1000 else ''}")


if __name__ == "__main__":
    main()
