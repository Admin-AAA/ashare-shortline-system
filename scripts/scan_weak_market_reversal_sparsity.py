"""离线扫描 weak-market-strong-stock-reversal 在真实非恐慌弱市日的候选稀疏度。

目的：判断该策略 60 天 0 命中是「结构性稀疏」（弱市里中期强势+缩量止跌本就罕见）
还是「配置过苛可放宽」。用 daily_kbars 原始列推导 setup 全部字段，不跑正式管线。
"""
from __future__ import annotations

import os

import pymysql
from collections import defaultdict

CONN = dict(host="127.0.0.1", port=3306, user="aowang", password=os.environ.get("ASHARE_DB_PASSWORD", ""),
            db="ashare_workbench", cursorclass=pymysql.cursors.DictCursor)

# 近60天非恐慌弱势日（limit_down_count<=100），含 05-27(ld39)/06-02(21)/06-03(13)/
# 06-04(15)/06-08(79)/06-10(51)/06-11(47)/06-17(4)/07-06(86)/07-07(54)/07-08(84)
WEAK_DATES = {
    "2026-05-27": 39, "2026-06-02": 21, "2026-06-03": 13, "2026-06-04": 15,
    "2026-06-08": 79, "2026-06-10": 51, "2026-06-11": 47, "2026-06-17": 4,
    "2026-07-06": 86, "2026-07-07": 54, "2026-07-08": 84,
}


def fetch_bars(cur, codes=None):
    cur.execute(
        """SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg,
                  turnover_rate, ma5, ma10, ma20, vol_ratio_5, vol_ratio_20, max_dd20
           FROM daily_kbars
           WHERE trade_date BETWEEN '2026-03-01' AND '2026-07-21'
           ORDER BY code, trade_date"""
    )
    by_code = defaultdict(list)
    for r in cur.fetchall():
        by_code[r["code"]].append(r)
    return by_code


def setup_match(bars, i):
    """bars 为某股按日期升序的 bar 列表，i 为当日下标。返回 (matched, detail)。"""
    if i < 60:
        return False, "insufficient_history"
    cur = bars[i]
    td = str(cur["trade_date"])
    if td not in WEAK_DATES:
        return False, "not_weak_day"

    close = float(cur["close"])
    pct = float(cur["pct_chg"])
    amount_yi = float(cur["amount"]) / 1e5  # 千元 -> 亿
    turnover = float(cur["turnover_rate"])
    max_dd20 = float(cur["max_dd20"]) if cur["max_dd20"] is not None else 0.0
    vol5 = float(cur["vol_ratio_5"]) if cur["vol_ratio_5"] is not None else None
    vol20 = float(cur["vol_ratio_20"]) if cur["vol_ratio_20"] is not None else None
    vol5_20 = vol5 / vol20 if (vol5 is not None and vol20) else None
    ma5 = float(cur["ma5"]) if cur["ma5"] is not None else None
    ma10 = float(cur["ma10"]) if cur["ma10"] is not None else None

    # chg20: 20日前收盘 -> 当日
    prev20 = float(bars[i - 20]["close"])
    chg20 = (close / prev20 - 1) * 100 if prev20 > 0 else -999.0
    # pos60: (close - min60_low) / (max60_high - min60_low) * 100
    win = bars[i - 59:i + 1]
    lo = min(float(b["low"]) for b in win)
    hi = max(float(b["high"]) for b in win)
    pos60 = (close - lo) / (hi - lo) * 100 if hi > lo else 50.0
    # off_high_pct: 相对60日最高
    off_high_pct = (close / hi - 1) * 100 if hi > 0 else -999.0

    support_ma5 = (ma5 is not None and close >= ma5 * 0.99)
    support_ma10 = (ma10 is not None and close >= ma10 * 0.99)

    checks = {
        "amount_yi>=2": amount_yi >= 2,
        "turnover 1-10": 1 <= turnover <= 10,
        "chg20>=15": chg20 >= 15,
        "pos60>=50": pos60 >= 50,
        "off_high_pct>=-12": off_high_pct >= -12,
        "0<=pct<3": 0 <= pct < 3,
        "max_dd20>=-14": max_dd20 >= -14,
        "vol5_20<=1.1": (vol5_20 is not None and vol5_20 <= 1.1),
        "ma_support": (support_ma5 or support_ma10),
    }
    matched = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    return matched, (f"chg20={chg20:.1f} pos60={pos60:.0f} pct={pct:.2f} vol5_20={vol5_20 and round(vol5_20,2)} "
                     f"dd20={max_dd20:.1f} | failed={failed}")


def main():
    conn = pymysql.connect(**CONN)
    cur = conn.cursor()
    by_code = fetch_bars(cur)
    print(f"加载 {len(by_code)} 只股票历史 K 线")

    day_hits = defaultdict(list)   # trade_date -> [(code, detail)]
    day_scanned = defaultdict(int)
    for code, bars in by_code.items():
        for i in range(len(bars)):
            td = str(bars[i]["trade_date"])
            if td not in WEAK_DATES:
                continue
            day_scanned[td] += 1
            matched, detail = setup_match(bars, i)
            if matched:
                day_hits[td].append((code, detail))

    print("\n=== 各非恐慌弱市日 setup 候选数 ===")
    total = 0
    for td in sorted(WEAK_DATES):
        hits = day_hits.get(td, [])
        total += len(hits)
        print(f"  {td} (ld={WEAK_DATES[td]:3d}): 扫描{day_scanned[td]:4d}只 -> 候选 {len(hits)} 只")
        for code, d in hits[:5]:
            print(f"      {code}  {d}")
    print(f"\n合计 {total} 个 setup 命中（11 个非恐慌弱市日）")
    print(f"日均候选: {total/len(WEAK_DATES):.1f} 只/弱市日")
    conn.close()


if __name__ == "__main__":
    main()
