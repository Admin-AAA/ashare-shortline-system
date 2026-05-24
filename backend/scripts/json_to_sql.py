"""将JSON K线数据转为SQL文件（多行INSERT，1000行/条），方便mysql CLI批量导入。"""

import json
import sys
from pathlib import Path

JSON_DIR = Path("/tmp/ashare_kbars_json")
SQL_FILE = Path("/tmp/ashare_kbars_import.sql")
CHUNK = 1000

SQL_HEADER = (
    "INSERT IGNORE INTO daily_kbars "
    "(code, trade_date, open, high, low, close, volume, amount, pct_chg, source, updated_at)\n"
    "VALUES\n"
)


def format_row(code, row):
    td = str(row[1])
    td_str = td[:4] + "-" + td[4:6] + "-" + td[6:8]
    return ("('{code}', '{td}', {open}, {high}, {low}, {close}, {vol}, {amt}, {pct}, 'tushare', NOW())"
            .format(
                code=code, td=td_str,
                open=float(row[2] or 0), high=float(row[3] or 0),
                low=float(row[4] or 0), close=float(row[5] or 0),
                vol=float(row[9] or 0),
                amt=float(row[10] or 0) * 1000,
                pct=float(row[8] or 0),
            ))


def main():
    files = sorted(JSON_DIR.glob("batch_*.json"))
    print(f"处理 {len(files)} 个JSON文件", flush=True)

    total_rows = 0
    buffer = []

    with open(SQL_FILE, "w", encoding="utf-8") as out:
        for fi, fp in enumerate(files):
            data = json.loads(fp.read_text())
            for entry in data:
                code = entry["code"]
                for row in entry["items"]:
                    buffer.append(format_row(code, row))
                    if len(buffer) >= CHUNK:
                        out.write(SQL_HEADER)
                        out.write(",\n".join(buffer))
                        out.write(";\n")
                        total_rows += len(buffer)
                        buffer = []

            print(f"  [{fi+1}/{len(files)}] {fp.name} -> {total_rows} rows so far", flush=True)

        if buffer:
            out.write(SQL_HEADER)
            out.write(",\n".join(buffer))
            out.write(";\n")
            total_rows += len(buffer)

    size_mb = SQL_FILE.stat().st_size / 1024 / 1024
    print(f"完成! 生成 {SQL_FILE} ({size_mb:.1f}MB), {total_rows} 行", flush=True)


if __name__ == "__main__":
    main()
