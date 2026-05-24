"""精简 K 线填充：为所有缺失 K 线的股票从 Tushare 补齐数据。"""

import logging
import sys
import time
from datetime import date, timedelta

import pymysql

sys.path.insert(0, ".")

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
}


def get_conn():
    return pymysql.connect(**DB_CONFIG)


def main():
    from app.services.tushare_client import (
        get_pro,
        clear_pro_cache,
        ts_code,
    )

    clear_pro_cache()
    pro = get_pro()

    def fetch_daily_kbars_fast(code: str, days: int = 250):
        """快速版：跳过 stk_factor_pro（太慢，23s/次），因子数据后续单独补。"""
        from datetime import date as dt_date, timedelta

        tsc = ts_code(code)
        sd = (dt_date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
        ed = dt_date.today().strftime("%Y%m%d")

        daily = pro.daily(ts_code=tsc, start_date=sd, end_date=ed)
        if daily is None or daily.empty:
            return []
        daily = daily.sort_values("trade_date").tail(days)
        daily_rows = {str(r["trade_date"]): r for _, r in daily.iterrows()}

        factor_map = {}
        try:
            factor = pro.stk_factor(ts_code=tsc, start_date=sd, end_date=ed)
            if factor is not None and not factor.empty:
                for _, r in factor.iterrows():
                    factor_map[str(r["trade_date"])] = dict(r)
        except Exception:
            pass

        basic_map = {}
        try:
            basic = pro.daily_basic(ts_code=tsc, start_date=sd, end_date=ed)
            if basic is not None and not basic.empty:
                for _, r in basic.iterrows():
                    basic_map[str(r["trade_date"])] = dict(r)
        except Exception:
            pass

        chip_map = {}
        try:
            chips = pro.cyq_perf(ts_code=tsc, start_date=sd, end_date=ed)
            if chips is not None and not chips.empty:
                for _, r in chips.iterrows():
                    chip_map[str(r["trade_date"])] = dict(r)
        except Exception:
            pass

        from app.services.tushare_client import _f, _fmt_date

        bars = []
        for td, row in daily_rows.items():
            fac = factor_map.get(td, {})
            bas = basic_map.get(td, {})
            chp = chip_map.get(td, {})

            bar = {
                "code": code,
                "trade_date": _fmt_date(str(row.get("trade_date", td))),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("vol")),
                "amount": _f(row.get("amount")) * 1000,
                "pct_chg": _f(row.get("pct_chg")),
                "turnover_rate": _f(bas.get("turnover_rate")) or _f(fac.get("turnover_rate")),
                "macd_dif": _f(fac.get("macd_dif")),
                "macd_dea": _f(fac.get("macd_dea")),
                "macd_bar": _f(fac.get("macd")),
                "kdj_k": _f(fac.get("kdj_k")),
                "kdj_d": _f(fac.get("kdj_d")),
                "kdj_j": _f(fac.get("kdj_j")),
                "rsi_6": _f(fac.get("rsi_6")),
                "rsi_12": _f(fac.get("rsi_12")),
                "rsi_24": _f(fac.get("rsi_24")),
                "boll_upper": _f(fac.get("boll_upper")),
                "boll_mid": _f(fac.get("boll_mid")),
                "boll_lower": _f(fac.get("boll_lower")),
                "cci": _f(fac.get("cci")),
                "adj_factor": _f(fac.get("adj_factor")),
                "asi": 0, "asit": 0, "atr": 0, "bbi": 0,
                "brar_ar": 0, "brar_br": 0, "dpo": 0,
                "ema_12": 0, "ema_50": 0,
                "ma_volume_5": 0, "ma_volume_10": 0,
                "mtm": 0, "obv": 0, "psy": 0, "trix": 0,
                "vr": 0, "wr_6": 0, "wr_10": 0,
            }
            bars.append(bar)
        return bars

    conn = get_conn()
    cur = conn.cursor()

    # 1. 获取所有 stock codes
    cur.execute("SELECT code FROM stocks ORDER BY code")
    all_codes = [row[0] for row in cur.fetchall()]
    logger.info("股票总数: %d", len(all_codes))

    # 2. 获取已有 K 线的 codes 及其日期数
    cur.execute(
        "SELECT code, COUNT(*) as cnt FROM daily_kbars GROUP BY code"
    )
    existing = {row[0]: row[1] for row in cur.fetchall()}
    logger.info("已有K线的股票: %d", len(existing))

    # 3. 找出缺失或不足的股票 （已有 >= MIN_BARS 条的跳过）
    MIN_BARS = 180
    codes_to_fill = []
    codes_skip = 0
    for code in all_codes:
        cnt = existing.get(code, 0)
        if cnt < MIN_BARS:
            codes_to_fill.append(code)
        else:
            codes_skip += 1

    logger.info(
        "需要补齐K线的股票: %d, 已跳过%d只(已有>=%d条)",
        len(codes_to_fill), codes_skip, MIN_BARS,
    )

    if not codes_to_fill:
        logger.info("所有股票K线已齐全，无需补齐")
        conn.close()
        return

    # 限制数量
    codes_to_fill = codes_to_fill[:5000]
    logger.info("实际处理: %d 只", len(codes_to_fill))

    # 4. 逐只填充
    total_inserted = 0
    conn.begin()

    for i, code in enumerate(codes_to_fill):
        # 带重试的 API 调用
        bars = []
        for attempt in range(3):
            try:
                bars = fetch_daily_kbars_fast(code, days=250)
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))  # 逐渐增加等待时间
                    # 清除连接缓存重试
                    clear_pro_cache()
                    globals()['pro'] = get_pro()
                else:
                    logger.warning("%s 失败: %s", code, e)
        if not bars:
            continue

        # 批量插入 — 只用云数据库实际存在的字段
        try:
            for bar in bars:
                # 字段名映射：fetch_daily_kbars → cloud DB columns
                bar.setdefault("macd_bar", bar.pop("macd", None))
                bar.setdefault("source", "tushare")
                cur.execute(
                    """INSERT IGNORE INTO daily_kbars
                    (code, trade_date, open, high, low, close, volume, amount, pct_chg,
                     turnover_rate,
                     macd_dif, macd_dea, macd_bar, kdj_k, kdj_d, kdj_j,
                     rsi_6, rsi_12, rsi_24, boll_upper, boll_mid, boll_lower, cci,
                     adj_factor, asi, asit, atr, bbi, brar_ar, brar_br, dpo,
                     ema_12, ema_50, ma_volume_5, ma_volume_10, mtm, obv, psy,
                     trix, vr, wr_6, wr_10, source, updated_at)
                    VALUES (%(code)s, %(trade_date)s, %(open)s, %(high)s, %(low)s,
                     %(close)s, %(volume)s, %(amount)s, %(pct_chg)s,
                     %(turnover_rate)s,
                     %(macd_dif)s, %(macd_dea)s, %(macd_bar)s, %(kdj_k)s, %(kdj_d)s, %(kdj_j)s,
                     %(rsi_6)s, %(rsi_12)s, %(rsi_24)s, %(boll_upper)s, %(boll_mid)s, %(boll_lower)s, %(cci)s,
                     %(adj_factor)s, %(asi)s, %(asit)s, %(atr)s, %(bbi)s, %(brar_ar)s, %(brar_br)s, %(dpo)s,
                     %(ema_12)s, %(ema_50)s, %(ma_volume_5)s, %(ma_volume_10)s, %(mtm)s, %(obv)s, %(psy)s,
                     %(trix)s, %(vr)s, %(wr_6)s, %(wr_10)s, 'tushare', NOW())
                    """,
                    bar,
                )

            total_inserted += len(bars)

        except Exception as e:
            logger.warning("%s 失败: %s", code, e)
            # 重连数据库
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
            conn = get_conn()
            cur = conn.cursor()
            conn.begin()

        if (i + 1) % 100 == 0:
            conn.commit()
            conn.begin()
            logger.info(
                "进度: %d/%d (%.1f%%), 已插入 %d bars",
                i + 1,
                len(codes_to_fill),
                100 * (i + 1) / len(codes_to_fill),
                total_inserted,
            )

        time.sleep(0.3)  # 避免过度请求 Tushare

    conn.commit()
    conn.close()

    logger.info("完成！共插入 %d bars", total_inserted)


if __name__ == "__main__":
    main()
