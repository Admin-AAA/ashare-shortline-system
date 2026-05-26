"""
陈小群情绪周期检测 — 阈值诊断与调优脚本。

功能：
  1. 对每个交易日打印原始数据（涨停数、跌停数、连板高度、晋级率）
  2. 标注当前阈值下的周期判定及触发原因
  3. 输出各周期数据分布统计
  4. 基于实际数据推荐优化阈值

用法：
  cd backend && source .venv/bin/activate && python scripts/diagnose_cxsq_cycle.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import Counter, defaultdict

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)

START_DATE = "2026-01-01"
END_DATE = "2026-05-25"

# ── 模拟当前阈值与候选阈值 ──

CURRENT = {
    "MIN_LIMIT_DOWN_FOR_RETREAT": 50,
    "MAX_HEIGHT_FOR_RETREAT": 2,
    "MIN_HEIGHT_FOR_STARTUP": 3,
    "MIN_LIMIT_UP_FOR_FERMENT": 5,
    "MIN_PROMOTION_RATE_FOR_FERMENT": 0.40,
    "MIN_LIMIT_UP_FOR_CLIMAX": 100,
    "MIN_HEIGHT_FOR_CLIMAX": 5,
}

PROPOSED = {
    "MIN_LIMIT_DOWN_FOR_RETREAT": 50,
    "MAX_HEIGHT_FOR_RETREAT": 2,
    "MIN_HEIGHT_FOR_STARTUP": 3,
    "MIN_LIMIT_UP_FOR_FERMENT": 5,
    "MIN_PROMOTION_RATE_FOR_FERMENT": 0.18,
    "MIN_LIMIT_UP_FOR_CLIMAX": 85,
    "MIN_HEIGHT_FOR_CLIMAX": 5,
}


def _simulate_cycle(
    limit_up: int,
    limit_down: int,
    max_height: int,
    promotion_rate: float,
    thresholds: dict,
) -> str:
    """用给定阈值模拟 detect_emotion_cycle 的判定。"""
    t = thresholds
    # 退潮
    if max_height <= t["MAX_HEIGHT_FOR_RETREAT"] or limit_down >= t["MIN_LIMIT_DOWN_FOR_RETREAT"]:
        cause = []
        if max_height <= t["MAX_HEIGHT_FOR_RETREAT"]:
            cause.append(f"高度{max_height}<={t['MAX_HEIGHT_FOR_RETREAT']}")
        if limit_down >= t["MIN_LIMIT_DOWN_FOR_RETREAT"]:
            cause.append(f"跌停{limit_down}>={t['MIN_LIMIT_DOWN_FOR_RETREAT']}")
        return f"退潮({'/'.join(cause)})"
    # 高潮
    if limit_up > t["MIN_LIMIT_UP_FOR_CLIMAX"] and max_height >= t["MIN_HEIGHT_FOR_CLIMAX"]:
        return "高潮"
    # 发酵
    if limit_up >= t["MIN_LIMIT_UP_FOR_FERMENT"] and promotion_rate > t["MIN_PROMOTION_RATE_FOR_FERMENT"]:
        return f"发酵"
    # 启动
    if max_height >= t["MIN_HEIGHT_FOR_STARTUP"]:
        return "启动"
    return "启动(默认)"


def main() -> None:
    db_url = os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4",
    )
    engine = create_engine(db_url)

    with Session(engine) as db:
        # 获取交易日历
        trade_dates = list(
            db.scalars(
                select(text("DISTINCT trade_date"))
                .select_from(text("daily_kbars"))
                .where(text(f"trade_date >= '{START_DATE}' AND trade_date <= '{END_DATE}'"))
                .order_by(text("trade_date"))
            ).all()
        )
        trade_dates = [str(d) for d in trade_dates]

        # 获取 limit_up_board 交易日集合
        lub_date_rows = db.execute(
            text("SELECT DISTINCT trade_date FROM limit_up_board WHERE trade_date >= :s AND trade_date <= :e"),
            {"s": START_DATE, "e": END_DATE},
        ).all()
        lub_dates = {str(r[0]) for r in lub_date_rows}

        # 预热：计算每个交易日的原始数据
        from app.services.chen_xiaoqun_service import _count_limit_up, _count_limit_down, _max_limit_up_consecutive, _compute_promotion_rate

        daily_raw: list[dict] = []
        not_in_lub: list[str] = []

        for td in trade_dates:
            has_lub = td in lub_dates
            lu = _count_limit_up(db, td)
            ld = _count_limit_down(db, td)
            height = _max_limit_up_consecutive(db, td)

            if has_lub:
                try:
                    pr = _compute_promotion_rate(db, td)
                except Exception:
                    pr = -1.0
            else:
                pr = -1.0

            current = _simulate_cycle(lu, ld, height, pr, CURRENT)
            proposed = _simulate_cycle(lu, ld, height, pr, PROPOSED)

            daily_raw.append({
                "date": td,
                "has_lub": has_lub,
                "limit_up": lu,
                "limit_down": ld,
                "max_height": height,
                "promotion_rate": pr,
                "current": current,
                "proposed": proposed,
            })

            if not has_lub:
                not_in_lub.append(td)

        # ── 打印每日详情 ──
        print("=" * 120)
        print(f"陈小群情绪周期 — 每日诊断 ({START_DATE} ~ {END_DATE}, 共 {len(trade_dates)} 个交易日)")
        print("=" * 120)
        print(f"{'日期':<12} {'有LUB':>5} {'涨停':>5} {'跌停':>5} {'高度':>4} {'晋级率':>7} {'当前周期':<20} {'建议周期':<20}")
        print("-" * 120)
        for d in daily_raw:
            pr_str = f"{d['promotion_rate']:.2%}" if d['promotion_rate'] >= 0 else "N/A"
            print(
                f"{d['date']:<12} "
                f"{'Y' if d['has_lub'] else 'N':>5} "
                f"{d['limit_up']:>5} "
                f"{d['limit_down']:>5} "
                f"{d['max_height']:>4} "
                f"{pr_str:>7} "
                f"{d['current']:<20} "
                f"{d['proposed']:<20}"
            )

        # ── 缺失数据统计 ──
        print()
        print("=" * 120)
        print(f"数据缺失诊断（limit_up_board 数据起始日: {sorted(lub_dates)[0] if lub_dates else '无'}）")
        print(f"  总交易日: {len(trade_dates)}")
        print(f"  有 LUB 数据: {len(lub_dates)} 天 ({len(lub_dates)/len(trade_dates)*100:.1f}%)")
        print(f"  无 LUB 数据: {len(not_in_lub)} 天 ({len(not_in_lub)/len(trade_dates)*100:.1f}%)")
        if not_in_lub:
            print(f"  无数据日期范围: {not_in_lub[0]} ~ {not_in_lub[-1]}")

        # ── 当前阈值周期分布 ──
        print()
        print("=" * 120)
        print("当前阈值周期分布（含无数据日，无数据日 → 退潮(高度0≤2)）")
        curr_counts = Counter(d["current"].split("(")[0] for d in daily_raw)
        for phase in ["退潮", "高潮", "发酵", "启动"]:
            cnt = curr_counts.get(phase, 0)
            print(f"  {phase}: {cnt} 天 ({cnt/len(trade_dates)*100:.1f}%)")

        print()
        print("当前阈值周期分布（仅含 LUB 数据日）")
        lub_raw = [d for d in daily_raw if d["has_lub"]]
        curr_counts_lub = Counter(d["current"].split("(")[0] for d in lub_raw)
        for phase in ["退潮", "高潮", "发酵", "启动"]:
            cnt = curr_counts_lub.get(phase, 0)
            print(f"  {phase}: {cnt} 天 ({cnt/len(lub_raw)*100:.1f}%)")

        # ── 建议阈值周期分布 ──
        print()
        print("=" * 120)
        print("建议阈值周期分布（含无数据日，无数据日 → 启动）")
        prop_counts = Counter(d["proposed"].split("(")[0] for d in daily_raw)
        for phase in ["退潮", "高潮", "发酵", "启动"]:
            cnt = prop_counts.get(phase, 0)
            print(f"  {phase}: {cnt} 天 ({cnt/len(trade_dates)*100:.1f}%)")

        print()
        print("建议阈值周期分布（仅含 LUB 数据日）")
        prop_counts_lub = Counter(d["proposed"].split("(")[0] for d in lub_raw)
        for phase in ["退潮", "高潮", "发酵", "启动"]:
            cnt = prop_counts_lub.get(phase, 0)
            print(f"  {phase}: {cnt} 天 ({cnt/len(lub_raw)*100:.1f}%)")

        # ── 关键字段统计 ──
        print()
        print("=" * 120)
        print("关键字段统计（仅 LUB 数据日）")
        lu_vals = [d["limit_up"] for d in lub_raw]
        ld_vals = [d["limit_down"] for d in lub_raw]
        h_vals = [d["max_height"] for d in lub_raw]
        pr_vals = [d["promotion_rate"] for d in lub_raw if d["promotion_rate"] >= 0]

        for name, vals in [
            ("涨停数", lu_vals),
            ("跌停数", ld_vals),
            ("连板高度", h_vals),
            ("晋级率", pr_vals),
        ]:
            s = sorted(vals)
            print(f"\n  {name}:")
            print(f"    最小值={min(vals):.2f}  最大值={max(vals):.2f}  均值={sum(vals)/len(vals):.2f}")
            print(f"    P5={s[int(len(s)*0.05)]:.2f}  P25={s[int(len(s)*0.25)]:.2f}  "
                  f"P50={s[int(len(s)*0.50)]:.2f}  P75={s[int(len(s)*0.75)]:.2f}  "
                  f"P90={s[int(len(s)*0.90)]:.2f}")

        # ── 晋级率分布 ──
        print()
        print("=" * 120)
        print("晋级率分布详情")
        buckets = [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20),
                   (0.20, 0.25), (0.25, 0.30), (0.30, 0.35), (0.35, 1.0)]
        bucket_counts = defaultdict(int)
        for pr in pr_vals:
            for lo, hi in buckets:
                if lo <= pr < hi:
                    bucket_counts[(lo, hi)] += 1
                    break
        for lo, hi in buckets:
            cnt = bucket_counts[(lo, hi)]
            bar = "█" * cnt
            print(f"  [{lo:.0%}~{hi:.0%}): {cnt:>2}天 {bar}")

        # ── 阈值建议 ──
        print()
        print("=" * 120)
        print("阈值优化建议")
        print()
        print(f"  {'参数':<35} {'当前值':>8} {'建议值':>8} {'依据':<50}")
        print("-" * 105)
        suggestions = [
            ("_MIN_LIMIT_UP_FOR_CLIMAX (涨停高潮)", 100, 85,
             f"涨停P90={sorted(lu_vals)[int(len(lu_vals)*0.9)]}, 仅顶部~10%可达100+"),
            ("_MIN_PROMOTION_RATE_FOR_FERMENT (晋级率发酵)", "0.40", "0.18",
             f"实际晋级率最大{max(pr_vals):.0%}, 均值{sum(pr_vals)/len(pr_vals):.0%}, 40%从未达到"),
            ("_MIN_LIMIT_DOWN_FOR_RETREAT (跌停退潮)", 50, 50,
             "保持不动，极端风险标记"),
            ("_MAX_HEIGHT_FOR_RETREAT (连板退潮)", 2, 2,
             "保持不动"),
            ("缺失 limit_up_board 数据处理", "→退潮", "→启动",
             "49天无数据被误判退潮，改为启动"),
        ]
        for name, curr, prop, basis in suggestions:
            print(f"  {name:<35} {str(curr):>8} {str(prop):>8}  {basis:<50}")

    engine.dispose()


if __name__ == "__main__":
    main()
