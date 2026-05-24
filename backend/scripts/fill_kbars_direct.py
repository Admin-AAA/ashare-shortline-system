"""极简K线导入 — 前台运行，requests.Session + 直接写 MySQL。"""

import logging
import sys
import time
from datetime import date, timedelta

import pymysql
import requests

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout
)
logger = logging.getLogger("fill")

TUSHARE_URL = "http://101.35.233.113:8020"
TUSHARE_TOKEN = "f8b05f8ee54f468220f2569afe56b4c027f1d388d164899339d394ad"

DB = {"host": "139.196.242.53", "port": 4407, "user": "aowang", "password": "1279990918",
      "database": "ashare_workbench", "charset": "utf8mb4", "connect_timeout": 10,
      "read_timeout": 30, "write_timeout": 60, "autocommit": False}


def tushare(session, api, **params):
    r = session.post(f"{TUSHARE_URL}/{api}",
                     json={"api_name": api, "token": TUSHARE_TOKEN, "params": params, "fields": ""},
                     timeout=15)
    r.raise_for_status()
    d = r.json()
    if d["code"] != 0:
        raise RuntimeError(d.get("msg", "Unknown"))
    data = d.get("data", {})
    return data.get("items", []) if data else []


def main():
    session = requests.Session()
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    # 需要补齐的 codes
    cur.execute("SELECT code FROM stocks ORDER BY code")
    all_codes = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT code, COUNT(*) FROM daily_kbars GROUP BY code")
    existing = {r[0]: r[1] for r in cur.fetchall()}
    needed = [c for c in all_codes if existing.get(c, 0) < 180]

    logger.info("总:%d 已有:%d 需补齐:%d", len(all_codes), len(existing), len(needed))
    if not needed:
        conn.close()
        return

    total, batch_ok, batch_total = 0, 0, 0

    for i, code in enumerate(needed):
        suffix = "SH" if code.startswith(("6", "9")) else "SZ"
        s = (date.today() - timedelta(days=750)).strftime("%Y%m%d")
        e = date.today().strftime("%Y%m%d")

        # 简单重试
        items = []
        for retry in range(3):
            try:
                items = tushare(session, "daily", ts_code=f"{code}.{suffix}", start_date=s, end_date=e)
                break
            except Exception as ex:
                if retry == 2:
                    logger.warning("  %s 跳过: %s", code, ex)
                else:
                    time.sleep(1 + retry)

        if items:
            # Tushare 返回字段: [ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount]
            items = sorted(items, key=lambda x: x[1])[-250:]
            for row in items:
                td = str(row[1])
                td_str = td[:4] + "-" + td[4:6] + "-" + td[6:8]
                cur.execute(
                    """INSERT IGNORE INTO daily_kbars
                    (code, trade_date, open, high, low, close, volume, amount, pct_chg, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'tushare', NOW())""",
                    [code, td_str,
                     float(row[2] or 0), float(row[3] or 0), float(row[4] or 0),
                     float(row[5] or 0),
                     float(row[9] or 0),
                     float(row[10] or 0) * 1000,
                     float(row[8] or 0)],
                )
            batch_ok += 1

        batch_total += 1

        if batch_total == 100:
            conn.commit()
            total += batch_ok
            pct = (i + 1) / len(needed) * 100
            logger.info("进度: %d/%d (%.1f%%), 本批OK:%d, 累计:%d", i+1, len(needed), pct, batch_ok, total)
            batch_ok, batch_total = 0, 0

        time.sleep(0.3)

    conn.commit()
    total += batch_ok
    conn.close()
    session.close()
    logger.info("完成！新增约 %d 只股票K线", total)


if __name__ == "__main__":
    main()
