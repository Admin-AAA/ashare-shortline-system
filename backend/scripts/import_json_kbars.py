"""从JSON文件批量导入K线到MySQL。"""

import json
import sys
import time
from pathlib import Path

import pymysql

DB = {
    "host": "139.196.242.53", "port": 4407, "user": "aowang", "password": "1279990918",
    "database": "ashare_workbench", "charset": "utf8mb4", "connect_timeout": 10,
    "read_timeout": 30, "write_timeout": 60, "autocommit": False,
}

JSON_DIR = Path("/tmp/ashare_kbars_json")
INSERT_SQL = """INSERT IGNORE INTO daily_kbars
    (code, trade_date, open, high, low, close, volume, amount, pct_chg, source, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'tushare', NOW())"""
CHUNK = 5000


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    files = sorted(JSON_DIR.glob("batch_*.json"))
    print(f"找到 {len(files)} 个JSON批次文件", flush=True)

    total_rows = 0
    total_stocks = 0
    t0 = time.monotonic()

    for fp in files:
        data = json.loads(fp.read_text())
        batch = []
        for entry in data:
            code = entry["code"]
            stock_rows = 0
            for row in entry["items"]:
                td = str(row[1])
                td_str = td[:4] + "-" + td[4:6] + "-" + td[6:8]
                batch.append((
                    code, td_str,
                    float(row[2] or 0), float(row[3] or 0),
                    float(row[4] or 0), float(row[5] or 0),
                    float(row[9] or 0),
                    float(row[10] or 0) * 1000,
                    float(row[8] or 0),
                ))
                stock_rows += 1
            if stock_rows > 0:
                total_stocks += 1

        # 分块插入
        for ci in range(0, len(batch), CHUNK):
            chunk = batch[ci:ci + CHUNK]
            cur.executemany(INSERT_SQL, chunk)
            conn.commit()

        total_rows += len(batch)
        print(f"  {fp.name}: {len(data)} stocks, {len(batch)} rows [{time.monotonic()-t0:.0f}s]", flush=True)

    cur.execute("SELECT COUNT(DISTINCT code) as stocks, COUNT(*) as cnt FROM daily_kbars")
    final = cur.fetchone()
    conn.close()
    print(f"完成! 导入 {total_stocks} 只股票, {total_rows} 行, 总计 {final[0]} 只/{final[1]} 行 [{time.monotonic()-t0:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
