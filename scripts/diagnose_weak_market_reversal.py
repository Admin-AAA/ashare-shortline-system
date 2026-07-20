"""诊断 weak-market-strong-stock-reversal 在非恐慌弱市日为何 0 候选。

逐条统计各 setup 条件的过滤漏斗 + 失败条件共现，定位互斥组合。
"""
from __future__ import annotations

import pymysql
from collections import defaultdict, Counter

CONN = dict(host="127.0.0.1", port=3306, user="aowang", password="1279990918",
            db="ashare_workbench", cursorclass=pymysql.cursors.DictCursor)

WEAK_DATES = {
    "2026-05-27": 39, "2026-06-02": 21, "2026-06-03": 13, "2026-06-04": 15,
    "2026-06-08": 79, "2026-06-10": 51, "2026-06-11": 47, "2026-06-17": 4,
    "2026-07-06": 86, "2026-07-07": 54, "2026-07-08": 84,
}


def f(v):
    return float(v) if v is not None else None


def main():
    conn = pymysql.connect(**CONN)
    cur = conn.cursor()
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

    # 漏斗：弱市日里，每加一条条件剩多少
    funnel = Counter()
    fail_pair = Counter()       # (condA, condB) 同时失败次数
    near_miss = []              # 只差 1-2 条的股票
    per_cond_fail = Counter()   # 单条件失败次数

    for code, bars in by_code.items():
        for i in range(60, len(bars)):
            cur = bars[i]
            td = str(cur["trade_date"])
            if td not in WEAK_DATES:
                continue
            close = f(cur["close"]); pct = f(cur["pct_chg"])
            amount_yi = f(cur["amount"]) / 1e5
            turnover = f(cur["turnover_rate"])
            max_dd20 = f(cur["max_dd20"]) or 0.0
            # 生产口径：vol5_20 = ma(volume,5) / ma(volume,20)（market_data.py:919）
            vols = [f(b["volume"]) or 0.0 for b in bars[i-19:i+1]]
            v5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else None
            v20 = sum(vols) / 20 if len(vols) >= 20 else None
            vol5_20 = v5 / v20 if (v5 is not None and v20) else None
            ma5 = f(cur["ma5"]); ma10 = f(cur["ma10"])
            prev20 = f(bars[i-20]["close"])
            chg20 = (close/prev20 - 1)*100 if prev20 else -999
            win = bars[i-59:i+1]
            lo = min(float(b["low"]) for b in win); hi = max(float(b["high"]) for b in win)
            pos60 = (close-lo)/(hi-lo)*100 if hi > lo else 50.0
            off_high = (close/hi - 1)*100 if hi else -999
            sup5 = ma5 is not None and close >= ma5*0.99
            sup10 = ma10 is not None and close >= ma10*0.99

            conds = {
                "amount>=2亿": amount_yi >= 2,
                "turnover1-10": turnover is not None and 1 <= turnover <= 10,
                "chg20>=15": chg20 >= 15,
                "pos60>=50": pos60 >= 50,
                "off_high>=-12": off_high >= -12,
                "0<=pct<3": pct is not None and 0 <= pct < 3,
                "max_dd20>=-14": max_dd20 >= -14,
                "vol5_20<=1.1": vol5_20 is not None and vol5_20 <= 1.1,
                "MA支撑": sup5 or sup10,
            }
            funnel["0_全部弱市日样本"] += 1
            passed = {k: v for k, v in conds.items()}
            # 顺序漏斗
            order = ["amount>=2亿","turnover1-10","chg20>=15","pos60>=50","off_high>=-12",
                     "0<=pct<3","max_dd20>=-14","vol5_20<=1.1","MA支撑"]
            for idx, name in enumerate(order, 1):
                if all(conds[o] for o in order[:idx]):
                    funnel[f"{idx}_{name}"] += 1
            failed = [k for k, v in conds.items() if not v]
            for fc in failed:
                per_cond_fail[fc] += 1
            for a in range(len(failed)):
                for b in range(a+1, len(failed)):
                    fail_pair[(failed[a], failed[b])] += 1
            if len(failed) <= 2 and "chg20>=15" in [k for k in conds]:
                if conds["chg20>=15"] and conds["pos60>=50"]:
                    near_miss.append((td, code, failed, f"chg20={chg20:.0f} pos60={pos60:.0f} pct={pct:.1f} vol5_20={vol5_20 and round(vol5_20,2)} dd20={max_dd20:.0f}"))

    print("=== 顺序过滤漏斗（弱市日样本逐条件递减）===")
    for k in sorted(funnel):
        print(f"  {k}: {funnel[k]}")

    print("\n=== 单条件失败次数（该条件卡掉多少弱市日样本）===")
    for c, n in per_cond_fail.most_common():
        print(f"  {c}: {n}")

    print("\n=== 失败条件共现 Top15（找互斥组合）===")
    for (a, b), n in fail_pair.most_common(15):
        print(f"  {a} × {b}: {n}")

    print(f"\n=== 离命中最近的股票（满足 chg20&pos60 双核心，仅差≤2条）共 {len(near_miss)} ===")
    for td, code, failed, d in near_miss[:30]:
        print(f"  {td} {code} failed={failed} | {d}")
    conn.close()


if __name__ == "__main__":
    main()
