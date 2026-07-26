"""稳定 K 线填充：canonical Provider Router + MySQL 批量写入。"""

import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pymysql

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.tushare_client import clear_pro_cache, get_pro  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("fill_kbars")

DB_CONFIG = {
    "host": "139.196.242.53",
    "port": 4407,
    "user": "aowang",
    "password": "1279990918",
    "database": "ashare_workbench",
    "charset": "utf8mb4",
    "connect_timeout": 10,
    "read_timeout": 30,
    "write_timeout": 30,
    "autocommit": False,
}


def _ts_code(code: str) -> str:
    return f"{code}.{'SH' if code.startswith(('6', '9')) else 'SZ'}"


def _f(value) -> float:
    if value is None or (isinstance(value, float) and value != value):
        return 0.0
    return float(value)


def _td(value) -> str:
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def tushare_query(pro, api_name: str, **params) -> pd.DataFrame:
    """调用 canonical Tushare facade，返回 DataFrame。"""
    return getattr(pro, api_name)(**params)


def fetch_daily_bars(pro, code: str, days: int = 250) -> list[dict]:
    """获取一只股票的日 K 线（含 turnover_rate，不含扩展因子）。"""
    tsc = _ts_code(code)
    sd = (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = date.today().strftime("%Y%m%d")

    daily = tushare_query(pro, "daily", ts_code=tsc, start_date=sd, end_date=ed)
    if daily.empty:
        return []
    daily = daily.sort_values("trade_date").tail(days)

    turnover_map = {}
    try:
        basic = tushare_query(pro, "daily_basic", ts_code=tsc, start_date=sd, end_date=ed)
        if not basic.empty:
            for _, row in basic.iterrows():
                turnover_map[str(row["trade_date"])] = _f(row.get("turnover_rate"))
    except Exception:
        pass

    bars = []
    for _, row in daily.iterrows():
        trade_date = str(row["trade_date"])
        bars.append(
            {
                "code": code,
                "trade_date": _td(trade_date),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("vol")),
                "amount": _f(row.get("amount")) * 1000,
                "pct_chg": _f(row.get("pct_chg")),
                "turnover_rate": turnover_map.get(trade_date, 0.0),
            }
        )
    return bars


def main() -> None:
    pro = get_pro()
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT code FROM stocks ORDER BY code")
    all_codes = [row[0] for row in cur.fetchall()]
    logger.info("股票总数: %d", len(all_codes))

    cur.execute("SELECT code, COUNT(*) as cnt FROM daily_kbars GROUP BY code")
    existing = {row[0]: row[1] for row in cur.fetchall()}
    logger.info("已有K线: %d 只", len(existing))

    codes_to_fill = [code for code in all_codes if existing.get(code, 0) < 180][:5000]
    logger.info("需补齐: %d 只 (已有 >=180 条的跳过)", len(codes_to_fill))

    total_inserted = 0
    batch_size = 100
    fail_streak = 0

    for batch_number, batch_start in enumerate(range(0, len(codes_to_fill), batch_size), start=1):
        batch = codes_to_fill[batch_start : batch_start + batch_size]
        logger.info(
            "批次 %d/%d: %d 只, 累计已插入 %d",
            batch_number,
            (len(codes_to_fill) + batch_size - 1) // batch_size,
            len(batch),
            total_inserted,
        )

        conn.begin()
        started = time.monotonic()
        batch_ok = 0
        batch_fail = 0

        for code in batch:
            for attempt in range(3):
                try:
                    bars = fetch_daily_bars(pro, code, days=250)
                    if not bars:
                        break
                    for bar in bars:
                        cur.execute(
                            """INSERT IGNORE INTO daily_kbars
                            (code, trade_date, open, high, low, close, volume, amount, pct_chg,
                             turnover_rate, source, updated_at)
                            VALUES (%(code)s, %(trade_date)s, %(open)s, %(high)s, %(low)s,
                             %(close)s, %(volume)s, %(amount)s, %(pct_chg)s,
                             %(turnover_rate)s, 'tushare', NOW())""",
                            bar,
                        )
                    batch_ok += 1
                    fail_streak = 0
                    break
                except Exception as exc:  # noqa: BLE001 - batch continues per code
                    if attempt < 2:
                        time.sleep(1 + attempt)
                    else:
                        logger.warning("  %s 跳过: %s", code, exc)
                        batch_fail += 1
                        fail_streak += 1

            if fail_streak > 5:
                logger.warning("  连续失败 %d 次，重建 Provider Session", fail_streak)
                clear_pro_cache()
                time.sleep(3)
                pro = get_pro()
                fail_streak = 0

            time.sleep(0.15)

        conn.commit()
        elapsed = time.monotonic() - started
        total_inserted += batch_ok * 250  # 近似，用于进度日志
        logger.info(
            "  本批: %d ok, %d fail, 耗时 %.1fs, 累计约 %d",
            batch_ok,
            batch_fail,
            elapsed,
            total_inserted,
        )
        time.sleep(1)

    cur.execute("SELECT COUNT(*) FROM daily_kbars")
    final_total = cur.fetchone()[0]
    conn.close()
    clear_pro_cache()
    logger.info("完成！daily_kbars 总数: %d", final_total)


if __name__ == "__main__":
    main()
