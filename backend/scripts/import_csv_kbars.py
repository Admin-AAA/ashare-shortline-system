"""导入本地历史K线CSV文件到MySQL — 300只SZ股票 2000-2026年数据。"""

import csv
import sys
import time
from pathlib import Path

import pymysql

DB = {
    "host": "139.196.242.53", "port": 4407, "user": "aowang", "password": "1279990918",
    "database": "ashare_workbench", "charset": "utf8mb4", "connect_timeout": 10,
    "read_timeout": 30, "write_timeout": 60, "autocommit": False,
}

CSV_DIR = Path("/Users/wangao/Downloads/历史k线")
BATCH = 5000

INSERT_SQL = """INSERT IGNORE INTO daily_kbars
    (code, trade_date, open, high, low, close, volume, amount, pct_chg, adj_factor, source, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'csv_import', NOW())"""


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    csv_files = sorted(CSV_DIR.glob("*.csv"))
    total_rows = 0
    total_files = 0
    skipped_files = 0

    t0 = time.monotonic()

    for fp in csv_files:
        code = fp.stem.split("_")[0]  # "000001_SZ" → "000001"
        if not code.isdigit() or len(code) != 6:
            skipped_files += 1
            continue

        batch = []
        with open(fp, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    batch.append((
                        code,
                        row["trade_date"],
                        float(row["open"] or 0),
                        float(row["high"] or 0),
                        float(row["low"] or 0),
                        float(row["close"] or 0),
                        float(row["vol"] or 0),
                        float(row["amount"] or 0) * 1000,  # 千元→元, 和Tushare一致
                        float(row["pct_chg"] or 0),
                        float(row["adj_factor"] or 1.0),
                    ))
                except (KeyError, ValueError):
                    continue

                if len(batch) >= BATCH:
                    cur.executemany(INSERT_SQL, batch)
                    conn.commit()
                    total_rows += len(batch)
                    batch = []

            if batch:
                cur.executemany(INSERT_SQL, batch)
                conn.commit()
                total_rows += len(batch)

        total_files += 1
        if total_files % 50 == 0:
            elapsed = time.monotonic() - t0
            print(f"进度: {total_files}/{len(csv_files)} 文件, {total_rows} 行, 耗时 {elapsed:.1f}s")

    cur.execute("SELECT COUNT(*) FROM daily_kbars")
    final = cur.fetchone()[0]
    conn.close()

    elapsed = time.monotonic() - t0
    print(f"完成! 导入 {total_files} 个文件, {total_rows} 行, 总行数 {final}, 耗时 {elapsed:.1f}s")
    if skipped_files:
        print(f"跳过 {skipped_files} 个非标准文件名")


if __name__ == "__main__":
    main()
