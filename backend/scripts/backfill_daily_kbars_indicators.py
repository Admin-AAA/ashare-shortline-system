"""
回补 daily_kbars 缺失的技术指标（T1 数据质量根因修复）。

问题（2026-07-21 自检发现）:
  daily_kbars 的衍生指标 ma5/ma10/ma20/ma60/vol_ratio_5/vol_ratio_20 在 2021-2025
  全部为 0/NULL（OHLC/amount/volume 本体 ~100% 完好），仅 2026 部分填充。
  根因：历史日K线的技术指标从未被本地计算回填。这导致：
    - 任何依赖 ma/vol_ratio 的历史回测（含 hammer/platform/limitup 等「OOS 滚动窗口」）
      实际只在 2026 单 period 内运行，所谓「样本外」全是 in-sample → 因子无法被诚实验证。
    - 本轮回测的「放量长上影试盘线」因子全样本仅 2026 信号，胜率 45.1% 无法判断噪声与否。

修复：这些指标均可由已存在的原始 OHLCV 直接计算，纯数据修复（不改 schema、不删数据、幂等）。
  - ma5/10/20/60 = close 的滚动均值
  - vol_ratio_5  = volume / 前 5 日均量（不含当日）
  - vol_ratio_20 = volume / 前 20 日均量（不含当日）
  turnover_rate 需要流通股本（本表无该字段）→ 不回填，避免编造。

用法:
  cd backend && source .venv/bin/activate
  python scripts/backfill_daily_kbars_indicators.py --dry-run     # 仅统计将更新的行数
  python scripts/backfill_daily_kbars_indicators.py --execute     # 实际写入（分批提交）
  python scripts/backfill_daily_kbars_indicators.py --execute --batch-size 20000
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MA_WINDOWS = [5, 10, 20, 60]
FILL_COLUMNS = ["ma5", "ma10", "ma20", "ma60", "vol_ratio_5", "vol_ratio_20"]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """对单只股票的日K序列（按 trade_date 升序）计算缺失指标。

    返回增加了 ma5/ma10/ma20/ma60/vol_ratio_5/vol_ratio_20 列的 DataFrame。
    仅计算值，不决定是否写入（写入由调用方按「原值为 NULL/0 才填」过滤）。
    """
    df = df.sort_values("trade_date").copy()
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    for w in MA_WINDOWS:
        df[f"ma{w}"] = close.rolling(w, min_periods=w).mean()
    # 量比 = 当日量 / 前 N 日均量（不含当日）
    prior5 = vol.rolling(5, min_periods=5).mean().shift(1)
    prior20 = vol.rolling(20, min_periods=20).mean().shift(1)
    df["vol_ratio_5"] = vol / prior5.replace(0, pd.NA)
    df["vol_ratio_20"] = vol / prior20.replace(0, pd.NA)
    return df


def _is_missing_series(s: pd.Series) -> pd.Series:
    """检测「原值为缺失」：NULL/NaN 或 0（ma/vol_ratio 不可能为 0，0 即未回填）。"""
    return s.isna() | (s == 0) | (s.astype(str).str.lower() == "nan")


def main() -> None:
    parser = argparse.ArgumentParser(description="回补 daily_kbars 缺失技术指标")
    parser.add_argument("--dry-run", action="store_true", help="仅统计将更新的行数，不写入")
    parser.add_argument("--execute", dest="execute", action="store_true", help="实际写入数据库")
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2026-12-31")
    args = parser.parse_args()
    if not args.dry_run and not args.execute:
        args.dry_run = True  # 默认安全：只统计

    from sqlalchemy import text
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        logger.info("加载 daily_kbars (%s ~ %s) ...", args.start_date, args.end_date)
        rows = db.execute(text(
            "SELECT id, code, trade_date, close, volume, "
            "ma5, ma10, ma20, ma60, vol_ratio_5, vol_ratio_20 "
            "FROM daily_kbars WHERE trade_date >= :s AND trade_date <= :e"
        ), {"s": args.start_date, "e": args.end_date}).fetchall()
        logger.info("加载 %d 行", len(rows))

        df = pd.DataFrame(rows, columns=[
            "id", "code", "trade_date", "close", "volume",
            "ma5", "ma10", "ma20", "ma60", "vol_ratio_5", "vol_ratio_20"])
        # 先算全部指标
        df["orig_ma5"], df["orig_ma10"], df["orig_ma20"] = df["ma5"], df["ma10"], df["ma20"]
        df["orig_ma60"], df["orig_vol_ratio_5"], df["orig_vol_ratio_20"] = (
            df["ma60"], df["vol_ratio_5"], df["vol_ratio_20"])

        computed = []
        for code, g in df.groupby("code", sort=False):
            computed.append(compute_indicators(g))
        full = pd.concat(computed, ignore_index=True)
        full["id"] = full["id"].astype(int)

        # 预计算每列的「需回填」布尔掩码（原值缺失 且 计算值非空）
        masks = {}
        total_cells = 0
        affected_rows = set()
        for col in FILL_COLUMNS:
            orig_col = "orig_" + col
            m = _is_missing_series(full[orig_col]) & full[col].notna()
            masks[col] = m
            total_cells += int(m.sum())
            affected_rows.update(full.loc[m, "id"].tolist())
        logger.info("将更新 %d 个单元格（%d 行受影响）", total_cells, len(affected_rows))

        if args.dry_run:
            print("\n=== DRY-RUN: 各列将更新单元格数 ===")
            for c in FILL_COLUMNS:
                print(f"  {c}: {int(masks[c].sum())}")
            print(f"  总计: {total_cells} 单元格 / {len(affected_rows)} 行")
            return

        # --execute：逐列向量化抽取 (val, id) 对，按 PK 批量 executemany（幂等、不覆盖已有值）
        logger.info("开始写入，batch_size=%d", args.batch_size)
        done = 0
        for col in FILL_COLUMNS:
            m = masks[col]
            if not m.any():
                continue
            ids = full.loc[m, "id"].tolist()
            vals = full.loc[m, col].round(6).tolist()
            pairs = [(v, i) for v, i in zip(vals, ids)]
            for i in range(0, len(pairs), args.batch_size):
                chunk = pairs[i:i + args.batch_size]
                db.execute(text(f"UPDATE daily_kbars SET {col} = :v, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
                           [{"v": v, "id": uid} for (v, uid) in chunk])
                db.commit()
                done += len(chunk)
                logger.info("  %s: 已写入 %d / %d", col, min(i + args.batch_size, len(pairs)), len(pairs))
        logger.info("回补完成 ✅ 共 %d 单元格", done)


if __name__ == "__main__":
    main()
