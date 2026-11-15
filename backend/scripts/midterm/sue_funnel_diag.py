"""SUE 族漏斗诊断：共享内核各条件逐层筛掉多少信号，定位「样本被谁卡住」。

现役 6 个 SUE 策略共享内核（以 §832 OCFS 为准）：
    liq & sue>=2.2 & q_sq>0 & pdd>0 & dtp>=50 & orv>=X & dtp>prev_dtp & amount>=1.5*amt20
再各自叠一个财务条件（OCFS / TD / SEXP / q_npta / cash_ratio）。
R12 样本只有 35~70 笔，门槛又被反复收紧过（SUE 2→2.2、orv 20→25→40→45、dtp 50→55→60）。
本脚本只数信号、不跑收益，用来判断「放宽哪一个次要条件」扩样本性价比最高。

只读本地 parquet，不写库、不改生产。
用法：PYTHONPATH=../..:. python sue_funnel_diag.py
"""
from __future__ import annotations

import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lt_research import PH  # noqa: E402
import lt_research66 as R66  # noqa: E402
import lt_research122 as S122  # noqa: E402

c = pl.col
PH = dict(PH)
PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")

# 内核条件（命名），基线 = 现役 §832 OCFS 口径：orv>=25 + vol>=1.5x
COND = {
    "liq": lambda: c("liq"),
    "sue": lambda: c("sue") >= 2.2,
    "q_sq": lambda: c("q_sq") > 0,
    "pdd": lambda: c("pdd") > 0,
    "dtp": lambda: c("dtp") >= 50,
    "accel": lambda: c("dtp") > c("prev_dtp"),
    "orv": lambda: c("orv") >= 25,
    "vol": lambda: c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20")),
}
BASE_KEYS = list(COND)

# 每个放宽选项 = (标签, 被替换/删除的条件名, 替代表达式或 None 表示直接去掉)
RELAX = [
    ("sue>=2.0（现2.2）", "sue", lambda: c("sue") >= 2.0),
    ("sue>=1.8（现2.2）", "sue", lambda: c("sue") >= 1.8),
    ("dtp>=40（现50）", "dtp", lambda: c("dtp") >= 40),
    ("dtp>=30（现50）", "dtp", lambda: c("dtp") >= 30),
    ("orv>=20（现25）", "orv", lambda: c("orv") >= 20),
    ("orv>=15（现25）", "orv", lambda: c("orv") >= 15),
    ("vol>=1.3x（现1.5x）", "vol", lambda: c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.3 * c("amt20"))),
    ("vol>=1.2x（现1.5x）", "vol", lambda: c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.2 * c("amt20"))),
    ("去掉 vol（不放量）", "vol", None),
    ("去掉 accel（不要求加速）", "accel", None),
    ("去掉 q_sq>0", "q_sq", None),
    ("去掉 pdd>0", "pdd", None),
]


def build():
    df = R66.prep().sort("code", "trade_date")
    gcols = S122.prep().select(["code", "trade_date", "sue", "q_sq", "pdd", "dtp", "orv", "prev_dtp"])
    return df.join(gcols, on=["code", "trade_date"], how="left").sort("code", "trade_date")


def cnt(df, expr, s, e):
    return int(df.filter(expr.fill_null(False) & c("trade_date").is_between(pl.lit(s), pl.lit(e))).height)


def core_expr(keys, override=None):
    """按 keys 顺序 AND 起来；override=(name, expr) 时把 name 那条换成 expr（None 表示删掉该条）。"""
    acc = None
    for k in keys:
        if override and override[0] == k:
            if override[1] is None:
                continue
            e = override[1]()
        else:
            e = COND[k]()
        acc = e if acc is None else (acc & e)
    return acc


def row(df, expr, label, wins):
    out = f"{label:26}"
    for _, ph in wins:
        out += f"{(cnt(df, expr, '2018-01-01', '2026-12-31') if ph is None else cnt(df, expr, *PH[ph])):>12}"
    return out


if __name__ == "__main__":
    df = build()
    print("列:", [x for x in df.columns if x in
                 ("liq", "sue", "q_sq", "pdd", "dtp", "orv", "prev_dtp", "amount", "amt20", "OCFS")], flush=True)

    wins = [("R12", "R12 2025-10~2026-10"), ("N3", "N3 2024-01~2026-10"),
            ("EXT", "EXT 2024-01~2025-09"), ("W1", "W1 2024"), ("全史", None)]
    hdr = f"{'条件':26}" + "".join(f"{w[0]:>12}" for w in wins)

    print("\n=== 内核逐层漏斗（信号日×股票 计数；orv 用现役 >=25）===")
    print(hdr)
    for i in range(1, len(BASE_KEYS) + 1):
        keys = BASE_KEYS[:i]
        print(row(df, core_expr(keys), "+".join(keys), wins), flush=True)

    base = core_expr(BASE_KEYS)
    print("\n=== 在完整内核之上，单独放宽/去掉一条后的信号数（与基线比增量）===")
    print(hdr)
    b = {w[0]: (cnt(df, base, "2018-01-01", "2026-12-31") if w[1] is None else cnt(df, base, *PH[w[1]])) for w in wins}
    print(row(df, base, "基线（现役内核）", wins), flush=True)
    for lab, name, alt in RELAX:
        expr = core_expr(BASE_KEYS, override=(name, alt))
        nums = [(cnt(df, expr, "2018-01-01", "2026-12-31") if w[1] is None else cnt(df, expr, *PH[w[1]])) for w in wins]
        line = f"{lab:26}"
        for w, num in zip(wins, nums):
            line += f"{('%d(+%d)' % (num, num - b[w[0]])):>12}"
        print(line, flush=True)

    print("\n=== 各叠加条件在「完整内核」之上还剩多少（决定各策略样本上限）===")
    print(hdr)
    for col in ("OCFS", "OCFSp"):
        if col not in df.columns:
            continue
        print(row(df, base & c(col).fill_null(False), col, wins), flush=True)
    print("\n注：TD/SEXP/q_npta/cash_ratio 不在 R66.prep 里，各自策略的叠加条件需在对应研究脚本中数。")

