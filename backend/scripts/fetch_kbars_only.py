"""极简K线填充 — Python直接调requests, 无pymysql(用文件缓冲), 1s间隔。"""

import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("fill")

TUSHARE_URL = "https://fastapic.stockai888.top"
TUSHARE_TOKEN = "eqyrJ71eIKjLoM12OeDLQOwRMUTuETs2ItnXAJitOhjTE8sLIjm8UuihObjXEs3zOFDKUI1PMrTmMp1yO9Tjl19"
OUT_DIR = Path("/tmp/ashare_kbars_batches")
BATCH_SIZE = 200
SLEEP_BETWEEN = 0.8  # 每只股票间隔


def tushare(session, api, **params):
    r = session.post(f"{TUSHARE_URL}/{api}", json={"api_name": api, "token": TUSHARE_TOKEN, "params": params, "fields": ""}, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0: raise RuntimeError(d.get("msg"))
    data = d.get("data", {})
    items = data.get("items", []) if data else []
    return items, data.get("fields", [])


def fetch_one(session, code):
    suffix = "SH" if code.startswith(("6", "9")) else "SZ"
    tsc = f"{code}.{suffix}"
    sd = (date.today() - timedelta(days=750)).strftime("%Y%m%d")
    ed = date.today().strftime("%Y%m%d")

    items, fields = tushare(session, "daily", ts_code=tsc, start_date=sd, end_date=ed)
    if not items:
        return []
    # 只取最近 250 条
    items = sorted(items, key=lambda x: x[fields.index("trade_date")])[-250:]
    return items


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open("/tmp/kbar_codes_needed.txt") as f:
        codes = [l.strip() for l in f if l.strip()]
    logger.info("需处理: %d 只", len(codes))

    session = requests.Session()
    total = 0
    fail_streak = 0

    for bi in range(0, len(codes), BATCH_SIZE):
        batch = codes[bi : bi + BATCH_SIZE]
        bt0 = time.monotonic()
        batch_data = []

        for code in batch:
            ok = False
            for attempt in range(3):
                try:
                    items = fetch_one(session, code)
                    if items:
                        batch_data.append({"code": code, "items": items})
                    ok = True
                    fail_streak = 0
                    break
                except Exception as e:
                    if attempt == 2:
                        logger.warning("  %s fail: %s", code, e)
                        fail_streak += 1
                    else:
                        time.sleep(1 + attempt)

            if ok:
                total += 1
            time.sleep(SLEEP_BETWEEN)

        # 保存批次
        if batch_data:
            fname = OUT_DIR / f"batch_{bi//BATCH_SIZE:04d}.json"
            fname.write_text(json.dumps(batch_data, ensure_ascii=False))

        elapsed = time.monotonic() - bt0
        logger.info("批次 %d/%d: %d stocks in %.1fs, 累计 %d",
                     bi//BATCH_SIZE + 1,
                     (len(codes) + BATCH_SIZE - 1)//BATCH_SIZE,
                     len(batch_data), elapsed, total)

        # 重建 session（避免长时间连接僵死）
        if fail_streak > 5:
            logger.warning("连续失败太多，重置 session")
            session.close()
            time.sleep(3)
            session = requests.Session()
            fail_streak = 0

    session.close()
    logger.info("完成！共拉取 %d 只股票，存于 %s", total, OUT_DIR)


if __name__ == "__main__":
    main()
