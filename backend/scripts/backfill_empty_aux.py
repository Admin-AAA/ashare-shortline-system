"""补齐空表：概念成分股、龙虎榜、资金流向、新闻公告、集合竞价。

运行时机：任意时间（周日也可拉取历史交易日数据）。
用法: python3 scripts/backfill_empty_aux.py
"""

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql
import requests
from app.services.tushare_client import get_pro, clear_pro_cache

DB = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "aowang",
    "password": os.environ.get("ASHARE_DB_PASSWORD", ""),
    "database": "ashare_workbench",
    "charset": "utf8mb4",
    "autocommit": False,
}

clear_pro_cache()
pro = get_pro()
conn = pymysql.connect(**DB)
cur = conn.cursor()

# ── 确定最近交易日 ──
today = date.today()
# 周五 = 4, 周六 = 5, 周日 = 6
if today.weekday() == 5:
    last_trade_date = today - timedelta(days=1)
elif today.weekday() == 6:
    last_trade_date = today - timedelta(days=2)
else:
    last_trade_date = today
trade_date_str = last_trade_date.strftime("%Y-%m-%d")
print(f"最近交易日: {last_trade_date} ({trade_date_str})")

# ── 热门股票池 ──
cur.execute("SELECT code FROM stock_quotes ORDER BY free_mv_yi IS NULL, free_mv_yi DESC LIMIT 300")
codes = [r[0] for r in cur.fetchall()]
print(f"热门股票: {len(codes)} 只")

# ═══════════════════════════════════════════════════════════════════════════
# 1. concept_sector_stock — 概念板块成分股
# ═══════════════════════════════════════════════════════════════════════════
print("\n── 1. 概念板块成分股 ──")
cur.execute(
    "SELECT sector_code, sector_name FROM concept_sector "
    "WHERE trade_date = %s ORDER BY heat_rank LIMIT 60",
    (trade_date_str,),
)
sectors = [(r[0], r[1]) for r in cur.fetchall()]
# 如果当天没有，取最近一天
if not sectors:
    cur.execute(
        "SELECT sector_code, sector_name FROM concept_sector ORDER BY heat_rank LIMIT 60"
    )
    sectors = [(r[0], r[1]) for r in cur.fetchall()]
print(f"  板块数: {len(sectors)}")

member_total = 0
for i, (sc, sn) in enumerate(sectors):
    try:
        members = __import__("app.services.tushare_client", fromlist=["fetch_concept_members"]).fetch_concept_members(sc)
        if not members:
            # 直接调
            df = pro.ths_member(ts_code=sc)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get("con_code", "")).split(".")[0]
                    if len(code) != 6:
                        continue
                    cur.execute(
                        """INSERT IGNORE INTO concept_sector_stock
                        (sector_code, code, trade_date, name, created_at)
                        VALUES (%s, %s, %s, %s, NOW())""",
                        (sc, code, trade_date_str, str(row.get("con_name", ""))),
                    )
                    member_total += 1
        else:
            for m in members:
                cur.execute(
                    """INSERT IGNORE INTO concept_sector_stock
                    (sector_code, code, trade_date, name, created_at)
                    VALUES (%s, %s, %s, %s, NOW())""",
                    (m["sector_code"], m["code"], trade_date_str, m.get("name", "")),
                )
                member_total += 1
    except Exception as e:
        print(f"  [{i+1}] {sn}({sc}): {e}")
    if (i + 1) % 20 == 0:
        conn.commit()
        print(f"  {i+1}/{len(sectors)} 累计 {member_total} 条")
conn.commit()
print(f"概念成分股: {member_total} 条")

# ═══════════════════════════════════════════════════════════════════════════
# 2. dragon_tiger — 龙虎榜（历史交易日）
# ═══════════════════════════════════════════════════════════════════════════
print("\n── 2. 龙虎榜 ──")
dt_total = 0
# 尝试最近 5 个交易日
for day_offset in range(5):
    dt = last_trade_date - timedelta(days=day_offset)
    dt_str = dt.strftime("%Y%m%d")
    try:
        df = pro.top_list(trade_date=dt_str)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("ts_code", "")).split(".")[0]
                if len(code) != 6:
                    continue
                cur.execute(
                    """INSERT IGNORE INTO dragon_tiger
                    (code, trade_date, name, reason, buy_amount_yi, sell_amount_yi, net_amount_yi, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (
                        code,
                        dt.isoformat(),
                        str(row.get("name", "")),
                        str(row.get("reason", "")),
                        round(float(row.get("l_buy") or 0) / 1e8, 4),
                        round(float(row.get("l_sell") or 0) / 1e8, 4),
                        round(float(row.get("net_amount") or 0) / 1e8, 4),
                    ),
                )
                dt_total += 1
            print(f"  {dt}: {len(df)} 条")
        else:
            print(f"  {dt}: 无数据")
    except Exception as e:
        print(f"  {dt}: {e}")
conn.commit()
print(f"龙虎榜: {dt_total} 条")

# ═══════════════════════════════════════════════════════════════════════════
# 3. capital_flow — 资金流向（历史交易日）
# ═══════════════════════════════════════════════════════════════════════════
print("\n── 3. 资金流向 ──")
cf_total = 0
for i, code in enumerate(codes):
    try:
        tsc = f'{code}.{"SH" if code.startswith(("6","9")) else "SZ"}'
        df = pro.moneyflow(ts_code=tsc, limit=5)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                td = str(row.get("trade_date", ""))
                cur.execute(
                    """INSERT IGNORE INTO capital_flow
                    (code, trade_date, main_net_inflow_yi, super_large_net_inflow_yi,
                     large_net_inflow_yi, medium_net_inflow_yi, small_net_inflow_yi,
                     main_net_inflow_ratio, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (
                        code,
                        td,
                        round(float(row.get("net_mf_amount") or 0) / 1e8, 4),
                        round(float(row.get("buy_elg_amount") or 0) / 1e8 - float(row.get("sell_elg_amount") or 0) / 1e8, 4),
                        round(float(row.get("buy_lg_amount") or 0) / 1e8 - float(row.get("sell_lg_amount") or 0) / 1e8, 4),
                        round(float(row.get("buy_md_amount") or 0) / 1e8 - float(row.get("sell_md_amount") or 0) / 1e8, 4),
                        round(float(row.get("buy_sm_amount") or 0) / 1e8 - float(row.get("sell_sm_amount") or 0) / 1e8, 4),
                        round(float(row.get("net_mf_vol") or 0), 2),
                    ),
                )
                cf_total += 1
    except Exception as e:
        if i < 5:
            print(f"  {code}: {e}")
    if (i + 1) % 100 == 0:
        conn.commit()
        print(f"  {i+1}/{len(codes)} 累计 {cf_total}")
conn.commit()
print(f"资金流向: {cf_total} 条")

# ═══════════════════════════════════════════════════════════════════════════
# 4. news_announcement — 新闻公告（eastmoney）
# ═══════════════════════════════════════════════════════════════════════════
print("\n── 4. 新闻公告 ──")
session = requests.Session()
news_total = 0
for i, code in enumerate(codes):
    try:
        # 公告
        ann_url = (
            "https://np-anotice-stock.eastmoney.com/api/security/ann"
            f"?page_size=10&page_index=1&ann_type=A"
            f"&stock_list={code}"
            "&column=NOTICE_COLUMN"
        )
        r = session.get(ann_url, timeout=(5, 10))
        r.raise_for_status()
        ann_data = r.json()
        ann_rows = ann_data.get("data", {}).get("list") or []
        for row in ann_rows:
            title = str(row.get("title_ch", ""))
            is_risk = any(
                kw in title
                for kw in ["风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报"]
            )
            cur.execute(
                """INSERT IGNORE INTO news_announcement
                (source, article_id, code, announce_date, title, summary, category, sentiment, is_risk_warning, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                (
                    "eastmoney",
                    str(row.get("art_code") or row.get("notice_id") or ""),
                    code,
                    str(row.get("notice_date", ""))[:10],
                    title,
                    str(row.get("summary", ""))[:500],
                    "announcement",
                    "negative" if is_risk else "neutral",
                    is_risk,
                ),
            )
            news_total += 1

        # 新闻
        news_url = (
            "https://np-anotice-stock.eastmoney.com/api/security/notify"
            f"?page_size=10&page_index=1"
            f"&stock_list={code}"
            "&column=NOTICE_COLUMN"
        )
        r = session.get(news_url, timeout=(5, 10))
        r.raise_for_status()
        news_data = r.json()
        news_rows = news_data.get("data", {}).get("list") or []
        for row in news_rows:
            title = str(row.get("title_ch", ""))
            is_negative = any(
                kw in title for kw in ["风险", "亏损", "下滑", "减持", "处罚", "跌停", "爆雷", "问询"]
            )
            is_positive = any(
                kw in title for kw in ["增长", "预增", "利好", "突破", "签约", "中标", "回购", "增持", "分红"]
            )
            if is_negative:
                sentiment = "negative"
            elif is_positive:
                sentiment = "positive"
            else:
                sentiment = "neutral"
            cur.execute(
                """INSERT IGNORE INTO news_announcement
                (source, article_id, code, announce_date, title, summary, category, sentiment, is_risk_warning, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                (
                    "eastmoney",
                    str(row.get("art_code") or row.get("notice_id") or ""),
                    code,
                    str(row.get("notice_date", ""))[:10],
                    title,
                    str(row.get("summary", ""))[:500],
                    "news",
                    sentiment,
                    is_negative,
                ),
            )
            news_total += 1
    except Exception as e:
        if i < 5:
            print(f"  {code}: {e}")
    if (i + 1) % 50 == 0:
        conn.commit()
        print(f"  {i+1}/{len(codes)} 累计 {news_total}")
conn.commit()
session.close()
print(f"新闻公告: {news_total} 条")

# ═══════════════════════════════════════════════════════════════════════════
# 5. auction_data — 集合竞价（历史交易日）
# ═══════════════════════════════════════════════════════════════════════════
print("\n── 5. 集合竞价 ──")
auc_total = 0
for i, code in enumerate(codes):
    try:
        tsc = f'{code}.{"SH" if code.startswith(("6","9")) else "SZ"}'
        df = pro.stk_auction(ts_code=tsc, trade_date=trade_date_str)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                cur.execute(
                    """INSERT IGNORE INTO auction_data
                    (code, trade_date, vol, price, amount, pre_close, turnover_rate, volume_ratio, float_share, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (
                        code,
                        trade_date_str,
                        float(row.get("vol") or 0),
                        float(row.get("price") or 0),
                        float(row.get("amount") or 0),
                        float(row.get("pre_close") or 0),
                        float(row.get("turnover_rate") or 0),
                        float(row.get("volume_ratio") or 0),
                        float(row.get("float_share") or 0),
                    ),
                )
                auc_total += 1
    except Exception as e:
        if i < 5:
            print(f"  {code}: {e}")
    if (i + 1) % 100 == 0:
        conn.commit()
        print(f"  {i+1}/{len(codes)} 累计 {auc_total}")
conn.commit()
print(f"集合竞价: {auc_total} 条")

cur.close()
conn.close()
pro.close()

print("\n=== 完成 ===")
print(f"concept_sector_stock: {member_total}")
print(f"dragon_tiger: {dt_total}")
print(f"capital_flow: {cf_total}")
print(f"news_announcement: {news_total}")
print(f"auction_data: {auc_total}")
