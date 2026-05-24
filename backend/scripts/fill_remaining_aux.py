"""补填剩余辅助数据：限售解禁、质押、回购、券商金股。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date, timedelta
from app.services.tushare_client import get_pro, clear_pro_cache, ts_code
import pymysql

DB = {"host": "139.196.242.53", "port": 4407, "user": "aowang", "password": "1279990918",
      "database": "ashare_workbench", "charset": "utf8mb4", "autocommit": False}

clear_pro_cache()
pro = get_pro()
conn = pymysql.connect(**DB)
cur = conn.cursor()

# Top 300 市值
cur.execute("SELECT code FROM stock_quotes ORDER BY free_mv_yi IS NULL, free_mv_yi DESC LIMIT 300")
codes = [r[0] for r in cur.fetchall()]
print(f"处理 {len(codes)} 只股票")

# ── share_floats ──
print("── 限售解禁 ──")
total = 0
for i, code in enumerate(codes):
    try:
        tsc = ts_code(code)
        df = pro.share_float(ts_code=tsc)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                cur.execute(
                    """INSERT IGNORE INTO share_floats
                    (code, ann_date, float_date, float_share, float_ratio, holder_name, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
                    (code, str(row.get("ann_date", "")), str(row.get("float_date", "")),
                     float(row.get("float_share") or 0), float(row.get("float_ratio") or 0),
                     str(row.get("holder_name", "")))
                )
                total += 1
    except Exception as e:
        if i < 5: print(f"  {code}: {e}")
        pass
    if (i+1) % 100 == 0:
        conn.commit()
        print(f"  {i+1}/{len(codes)} 累计 {total}")
conn.commit()
print(f"限售解禁: {total} 条")

# ── pledge_stats ──
print("── 股权质押 ──")
total = 0
for i, code in enumerate(codes):
    try:
        tsc = ts_code(code)
        df = pro.pledge_stat(ts_code=tsc)
        if df is not None and not df.empty:
            row = df.sort_values("end_date").iloc[-1]
            cur.execute(
                """INSERT IGNORE INTO pledge_stats
                (code, end_date, pledge_count, unrest_pledge, rest_pledge, pledge_ratio, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
                (code, str(row.get("end_date", "")), int(row.get("pledge_count") or 0),
                 float(row.get("unrest_pledge") or 0), float(row.get("rest_pledge") or 0),
                 float(row.get("pledge_ratio") or 0))
            )
            total += 1
    except Exception as e:
        if i < 5: print(f"  {code}: {e}")
        pass
    if (i+1) % 100 == 0:
        conn.commit()
        print(f"  {i+1}/{len(codes)} 累计 {total}")
conn.commit()
print(f"股权质押: {total} 条")

# ── repurchases ──
print("── 回购 ──")
total = 0
for i, code in enumerate(codes):
    try:
        tsc = ts_code(code)
        df = pro.repurchase(ts_code=tsc)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                cur.execute(
                    """INSERT IGNORE INTO repurchases
                    (code, ann_date, end_date, vol, amount, high_limit, low_limit, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (code, str(row.get("ann_date", "")), str(row.get("end_date", "")),
                     float(row.get("vol") or 0), float(row.get("amount") or 0),
                     float(row.get("high_limit") or 0), float(row.get("low_limit") or 0))
                )
                total += 1
    except Exception as e:
        if i < 5: print(f"  {code}: {e}")
        pass
    if (i+1) % 100 == 0:
        conn.commit()
        print(f"  {i+1}/{len(codes)} 累计 {total}")
conn.commit()
print(f"回购: {total} 条")

# ── broker_recommends ──
print("── 券商金股 ──")
total = 0
today = date.today()
for m_offset in range(3):
    m = (today.replace(day=1) - timedelta(days=m_offset * 31)).strftime("%Y%m")
    try:
        df = pro.broker_recommend(month=m)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("ts_code", "")).split(".")[0]
                if len(code) != 6: continue
                cur.execute(
                    """INSERT IGNORE INTO broker_recommends
                    (code, name, month, broker, created_at)
                    VALUES (%s, %s, %s, %s, NOW())""",
                    (code, str(row.get("name", "")), str(row.get("month", m)),
                     str(row.get("broker", "")))
                )
                total += 1
    except Exception as e:
        print(f"  {m}: {e}")
        pass
conn.commit()
print(f"券商金股: {total} 条")

conn.close()
pro.close()
print("完成!")
