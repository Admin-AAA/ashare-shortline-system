# -*- coding: utf-8 -*-
"""daily_kbars 指标回补脚本的纯逻辑单测 + 可选 DB 正确性校验。

纯逻辑测试（compute_indicators）不依赖 DB，始终运行；DB 正确性校验在 MySQL 不可达时
自动 skip。DB 校验只做「读后比对」（用 compute_indicators 计算结果对照原始 close 的滚动均值），
不写入，避免污染实时库。

设计纪律（见 lessons.md [T1]）：本脚本修复的是「2021-2025 日K衍生指标全为 NULL」的数据质量
根因——它使所有历史回测的「OOS 滚动窗口」实际都落在 2026 单 period 内，因子无法被诚实验证。
回补仅填 NULL/0 格子（幂等、不覆盖已有值），不改 schema、不删数据。
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_daily_kbars_indicators import compute_indicators  # noqa: E402


def _make_stock(closes, volumes, code="T00001"):
    n = len(closes)
    base = pd.Timestamp("2024-01-01")
    return pd.DataFrame({
        "id": list(range(1, n + 1)),
        "code": [code] * n,
        "trade_date": [(base + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)],
        "close": closes,
        "volume": volumes,
        "ma5": [np.nan] * n, "ma10": [np.nan] * n, "ma20": [np.nan] * n,
        "ma60": [np.nan] * n, "vol_ratio_5": [np.nan] * n, "vol_ratio_20": [np.nan] * n,
    })


def test_compute_indicators_ma():
    # 干净等差数列 closes = 10,11,...,30（21 个），期望易算
    closes = [float(x) for x in range(10, 31)]
    vols = [100] * len(closes)
    df = compute_indicators(_make_stock(closes, vols))
    # ma5 第5日(index4) = mean(10..14) = 12.0
    assert df["ma5"].iloc[4] == pytest.approx(12.0, abs=1e-6)
    # ma5 第10日(index9) = mean(15..19) = 17.0
    assert df["ma5"].iloc[9] == pytest.approx(17.0, abs=1e-6)
    # ma20 第20日(index19) = mean(10..29) = 19.5
    assert df["ma20"].iloc[19] == pytest.approx(19.5, abs=1e-6)
    # ma20 第21日(index20) = mean(11..30) = 20.5
    assert df["ma20"].iloc[20] == pytest.approx(20.5, abs=1e-6)
    # 前4日 ma 应为 NaN（min_periods）
    assert df["ma5"].iloc[3] != df["ma5"].iloc[3]  # NaN


def test_compute_indicators_vol_ratio():
    closes = [10.0] * 10
    vols = [100, 100, 100, 100, 100, 200, 100, 100, 100, 100]
    df = compute_indicators(_make_stock(closes, vols))
    # 第6日(index5) volume=200, 前5日均量(不含当日)=100 -> vol_ratio_5 = 200/100 = 2.0
    assert df["vol_ratio_5"].iloc[5] == pytest.approx(2.0, abs=1e-6)
    # 第1日(index0) 无前5日 -> NaN
    assert df["vol_ratio_5"].iloc[0] != df["vol_ratio_5"].iloc[0]


def test_compute_indicators_idempotent_shape():
    closes = list(range(1, 31))
    vols = [50] * 30
    df = compute_indicators(_make_stock(closes, vols))
    assert len(df) == 30
    assert df["ma60"].iloc[29] != df["ma60"].iloc[29]  # 仅30日，ma60 仍 NaN
    assert df["ma20"].iloc[19] == pytest.approx(10.5, abs=1e-6)  # mean(1..20)


def _db_reachable():
    try:
        from sqlalchemy import text
        from app.db.session import engine
        if engine.dialect.name != "mysql":
            return False
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="MySQL 不可达（无网/CI 环境）")
def test_db_indicators_consistency_readonly():
    """DB 正确性校验（只读）：取一只股票，用 compute_indicators 计算 ma20，
    对照原始 close 的 pandas 滚动均值，断言一致。不写入。"""
    from sqlalchemy import text
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        code = db.execute(text(
            "SELECT code FROM daily_kbars GROUP BY code "
            "HAVING COUNT(*) >= 60 ORDER BY COUNT(*) DESC LIMIT 1"
        )).scalar()
        assert code is not None
        rows = db.execute(text(
            "SELECT id, code, trade_date, close, volume FROM daily_kbars "
            "WHERE code = :c ORDER BY trade_date ASC"
        ), {"c": code}).fetchall()
        df = pd.DataFrame(rows, columns=["id", "code", "trade_date", "close", "volume"])
        df["ma5"] = df["ma10"] = df["ma20"] = df["ma60"] = np.nan
        df["vol_ratio_5"] = df["vol_ratio_20"] = np.nan
        computed = compute_indicators(df)
        # 对照：pandas 独立重算 ma20
        ref = df["close"].astype(float).rolling(20, min_periods=20).mean()
        # 取一个有足够历史的下标
        idx = 40
        assert computed["ma20"].iloc[idx] == pytest.approx(float(ref.iloc[idx]), abs=1e-4)
