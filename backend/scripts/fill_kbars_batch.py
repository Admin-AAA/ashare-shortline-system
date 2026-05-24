"""稳定 K线填充 — 使用 requests.Session 复用连接 + 严格超时 + 重试。"""

import logging
import sys
import time
from datetime import date, timedelta

import pandas as pd
import pymysql
import requests

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("fill_kbars")

TUSHARE_URL = "http://101.35.233.113:8020"
TUSHARE_TOKEN = "f8b05f8ee54f468220f2569afe56b4c027f1d388d164899339d394ad"
TUSHARE_TIMEOUT = 15

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


def _f(v) -> float:
    if v is None or (isinstance(v, float) and (v != v)):
        return 0.0
    return float(v)


def _td(val) -> str:
    return str(val)[:4] + "-" + str(val)[4:6] + "-" + str(val)[6:8]


def tushare_query(session: requests.Session, api_name: str, **params) -> pd.DataFrame:
    """调用 Tushare API，返回 DataFrame。"""
    req_params = {
        "api_name": api_name,
        "token": TUSHARE_TOKEN,
        "params": params,
        "fields": "",
    }
    resp = session.post(
        f"{TUSHARE_URL}/{api_name}",
        json=req_params,
        timeout=TUSHARE_TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Tushare {api_name} error: {result.get('msg')}")
    data = result.get("data")
    if not data or "items" not in data or not data["items"]:
        return pd.DataFrame()
    return pd.DataFrame(data["items"], columns=data["fields"])


def fetch_daily_bars(session: requests.Session, code: str, days: int = 250) -> list[dict]:
    """获取一只股票的日K线（含 turnover_rate，不含扩展因子）。"""
    tsc = _ts_code(code)
    sd = (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = date.today().strftime("%Y%m%d")

    daily = tushare_query(session, "daily", ts_code=tsc, start_date=sd, end_date=ed)
    if daily.empty:
        return []
    daily = daily.sort_values("trade_date").tail(days)

    # turnover_rate
    turnover_map = {}
    try:
        basic = tushare_query(session, "daily_basic", ts_code=tsc, start_date=sd, end_date=ed)
        if not basic.empty:
            for _, r in basic.iterrows():
                turnover_map[str(r["trade_date"])] = _f(r.get("turnover_rate"))
    except Exception:
        pass

    bars = []
    for _, row in daily.iterrows():
        td = str(row["trade_date"])
        bars.append({
            "code": code,
            "trade_date": _td(td),
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "close": _f(row.get("close")),
            "volume": _f(row.get("vol")),
            "amount": _f(row.get("amount")) * 1000,
            "pct_chg": _f(row.get("pct_chg")),
            "turnover_rate": turnover_map.get(td, 0.0),
        })
    return bars


def main():
    session = requests.Session()
    session.headers["Content-Type"] = "application/json"

    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()

    # 1. 股票列表
    cur.execute("SELECT code FROM stocks ORDER BY code")
    all_codes = [row[0] for row in cur.fetchall()]
    logger.info("股票总数: %d", len(all_codes))

    # 2. 已有K线
    cur.execute("SELECT code, COUNT(*) as cnt FROM daily_kbars GROUP BY code")
    existing = {row[0]: row[1] for row in cur.fetchall()}
    logger.info("已有K线: %d 只", len(existing))

    # 3. 需补齐
    codes_to_fill = [c for c in all_codes if existing.get(c, 0) < 180]
    logger.info("需补齐: %d 只 (已有 >=180 条的跳过)", len(codes_to_fill))
    codes_to_fill = codes_to_fill[:5000]

    total_inserted = 0
    batch_size = 100
    fail_streak = 0

    for bi, batch_start in enumerate(range(0, len(codes_to_fill), batch_size)):
        batch = codes_to_fill[batch_start : batch_start + batch_size]
        logger.info("批次 %d/%d: %d 只, 累计已插入 %d",
                     bi + 1, (len(codes_to_fill) + batch_size - 1) // batch_size,
                     len(batch), total_inserted)

        conn.begin()
        bt0 = time.monotonic()
        batch_ok = 0
        batch_fail = 0

        for code in batch:
            for attempt in range(3):
                try:
                    bars = fetch_daily_bars(session, code, days=250)
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
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1 + attempt)
                    else:
                        logger.warning("  %s 跳过: %s", code, e)
                        batch_fail += 1
                        fail_streak += 1

            # 连续失败太多 → 可能网络问题，重建 Session
            if fail_streak > 5:
                logger.warning("  连续失败 %d 次，重建 Session", fail_streak)
                session.close()
                time.sleep(3)
                session = requests.Session()
                session.headers["Content-Type"] = "application/json"
                fail_streak = 0

            time.sleep(0.15)  # 请求间隔

        conn.commit()
        elapsed = time.monotonic() - bt0
        logger.info("  本批: %d ok, %d fail, 耗时 %.1fs, 累计 %d",
                     batch_ok, batch_fail, elapsed, total_inserted + sum(1 for _ in []) or batch_ok)

        # 实际更新累计
        total_inserted += batch_ok * 250  # 近似

        time.sleep(1)  # 批次间隔

    # 精确计数
    cur.execute("SELECT COUNT(*) FROM daily_kbars")
    final_total = cur.fetchone()[0]
    conn.close()
    session.close()
    logger.info("完成！daily_kbars 总数: %d", final_total)


if __name__ == "__main__":
    main()
