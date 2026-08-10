"""回填 stock_metadata_history：时点化股票元数据区间表。

重建策略（时点元数据能做的最好程度，诚实标注）：
- ST 状态：从 Tushare namechange（名称变更日志）精确重建时间区间。change_reason
  含 ST/*ST/撤销ST/从ST变为*ST/叠加ST/退市整理期 等，start_date 是变更生效日。
- industry / market / list_date / name：无公开逐日历史快照，取 stocks 表当前态，
  全期有效（effective_from = list_date 或窗口起点，effective_to = NULL）。

区间不重叠（stock_metadata.resolve_stock_metadata_as_of 硬约束）。

用法：
  python scripts/backfill_stock_metadata_history.py --dry-run   # 预览不写库
  python scripts/backfill_stock_metadata_history.py             # 写入
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
from datetime import date

import pandas as pd
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.tables import StockMetadataHistory

SOURCE_VERSION = "stocks+namechange/2026-08-10/v1"

# 推导 ST 状态的 change_reason 集合（出现 ST/*ST 名称 → 该区间 is_st=True）
_ST_MARKERS = ("ST", "*ST", "叠加ST", "从ST变为*ST", "退市整理期")
_ST_ENTER_REASONS = {"ST", "*ST", "叠加ST", "从ST变为*ST", "退市整理期", "撤消*ST并实行ST"}
_ST_EXIT_REASONS = {"撤销ST", "撤销*ST"}


def _is_st_name(name: str) -> bool:
    n = str(name or "")
    return "ST" in n.upper()


def _fetch_namechange(pro, start_year: int, end_year: int) -> pd.DataFrame:
    frames = []
    for yr in range(start_year, end_year + 1):
        df = pro.namechange(start_date=f"{yr}0101", end_date=f"{yr}1231")
        if df is not None and len(df) > 0:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates()


def _normalize_code(ts_code: str) -> str:
    return str(ts_code).split(".")[0]


def _is_valid_code(code: str) -> bool:
    """标准 6 位数字股票代码（过滤 T600018 这类异常前缀）。"""
    return len(code) == 6 and code.isdigit()


def _fetch_delisted(pro) -> pd.DataFrame:
    df = pro.stock_basic(
        exchange="", list_status="D",
        fields="ts_code,name,industry,market,list_date,delist_date",
    )
    return df if df is not None else pd.DataFrame()


def build_intervals(db, pro) -> list[dict]:
    """为每只股票构建不重叠的元数据区间。"""
    # 1. 当前态基线（stocks 表）
    rows = db.execute(
        text("SELECT code, name, industry, market, list_date, is_st FROM stocks")
    ).fetchall()
    # stocks.is_st 字段与 name 不一致（000010 name=*ST美丽 但 is_st=0），不可靠。
    # 当前 ST 态以 name 中是否含 ST 字样为准（与 namechange 推导同源），is_st 仅作 fallback。
    current = {
        str(r[0]): {
            "name": str(r[1] or ""),
            "industry": str(r[2]) if r[2] else None,
            "market": str(r[3]) if r[3] else None,
            "list_date": str(r[4]) if r[4] else None,
            "is_st": _is_st_name(r[1]) if str(r[1] or "").strip() else bool(r[5]),
        }
        for r in rows
    }

    # 1b. 退市股（幸存者偏差防护）：当前 stocks 表不含，但历史 K 线里有。
    # industry 字段退市股为空，用 None；market 归一化。
    delisted = _fetch_delisted(pro)
    for _, row in delisted.iterrows():
        code = _normalize_code(row["ts_code"])
        if code in current:
            continue  # 已上市，以 stocks 为准
        ld = str(row["list_date"]) if row["list_date"] else None
        if ld and len(ld) == 8:
            ld = f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}"
        else:
            ld = None
        dd = str(row["delist_date"]) if row["delist_date"] else None
        if dd and len(dd) == 8:
            dd = f"{dd[:4]}-{dd[4:6]}-{dd[6:8]}"
        else:
            dd = None
        current[code] = {
            "name": str(row["name"] or ""),
            "industry": str(row["industry"]) if row["industry"] and str(row["industry"]) != "None" else None,
            "market": (str(row["market"])[:2] if row["market"] else None),
            "list_date": ld,
            "is_st": _is_st_name(row["name"]),
            "_delist_date": dd,
        }

    # 2. ST 变更事件（namechange）
    nc = _fetch_namechange(pro, 2015, date.today().year)
    st_events: dict[str, list[tuple[str, bool]]] = {}
    if len(nc) > 0:
        st_df = nc[nc["change_reason"].astype(str).str.contains("ST|st|退市", na=False)]
        for _, row in st_df.iterrows():
            code = _normalize_code(row["ts_code"])
            reason = str(row["change_reason"])
            start = str(row["start_date"]) if row["start_date"] else None
            if not start or len(start) != 8:
                continue
            start_iso = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
            # 名称含 ST → 该日起 is_st=True；否则（撤销ST/撤销*ST 后无 ST 名）→ False
            entering = reason in _ST_ENTER_REASONS or _is_st_name(row["name"])
            if reason in _ST_EXIT_REASONS and not _is_st_name(row["name"]):
                entering = False
            st_events.setdefault(code, []).append((start_iso, entering))

    # 3. 构建区间
    window_start = "2015-01-01"
    intervals: list[dict] = []
    for code, meta in current.items():
        if not _is_valid_code(code):
            continue
        eff_from = meta["list_date"] or window_start
        if eff_from < window_start:
            eff_from = window_start
        events = sorted(set(st_events.get(code, [])))
        delist_to = meta.get("_delist_date")  # 退市股截止日
        # 退化区间：退市早于窗口起点 → 该股票在窗口内无任何有效元数据，跳过
        if delist_to and delist_to < eff_from:
            continue
        if not events:
            # 无 ST 变更 → 单区间全期
            intervals.append(
                {
                    "code": code,
                    "name": meta["name"],
                    "industry": meta["industry"],
                    "market": meta["market"],
                    "list_date": meta["list_date"],
                    "is_st": meta["is_st"],
                    "effective_from": eff_from,
                    "effective_to": delist_to,
                }
            )
            continue

        # 有 ST 变更 → 每个变更点的 is_st 由该事件的 name 直接决定（含 ST 字样=True），
        # 不做"翻转"假设（连续 ST 事件如"撤消*ST并实行ST"仍是 ST）。
        # 变更点 list 是 (start_iso, is_st_at_that_point)。首段（第一个变更点之前）
        # 用变更点的反向；相邻同状态变更点去重合并。
        point_states: list[tuple[str, bool]] = []
        for pt, st in sorted(events):
            if pt < eff_from:
                continue
            if point_states and point_states[-1][1] == st:
                continue  # 同状态连续事件，跳过
            point_states.append((pt, st))

        # 段：每段 is_st 由 point_states 决定；eff_from..first_pt 用 first_pt 段的反向。
        segs: list[tuple[str, str | None, bool]] = []
        if point_states:
            first_pt, first_st = point_states[0]
            if first_pt > eff_from:
                head_to = (pd.Timestamp(first_pt) - pd.Timedelta(days=1)).date().isoformat()
                intervals.append(
                    {
                        "code": code,
                        "name": meta["name"],
                        "industry": meta["industry"],
                        "market": meta["market"],
                        "list_date": meta["list_date"],
                        "is_st": not first_st,
                        "effective_from": eff_from,
                        "effective_to": head_to,
                    }
                )
            for idx, (pt, st) in enumerate(point_states):
                nxt = point_states[idx + 1][0] if idx + 1 < len(point_states) else None
                seg_to = (pd.Timestamp(nxt) - pd.Timedelta(days=1)).date().isoformat() if nxt else delist_to
                # 退市股：最后一段也不能超过 delist_to
                if delist_to and (seg_to is None or seg_to > delist_to):
                    seg_to = delist_to
                # 跳过完全落在退市之后的段
                if delist_to and pt > delist_to:
                    continue
                intervals.append(
                    {
                        "code": code,
                        "name": meta["name"],
                        "industry": meta["industry"],
                        "market": meta["market"],
                        "list_date": meta["list_date"],
                        "is_st": st,
                        "effective_from": pt,
                        "effective_to": seg_to,
                    }
                )
    return intervals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-version", default=SOURCE_VERSION)
    args = parser.parse_args()

    from app.services.tushare_client import get_pro

    db = SessionLocal()
    try:
        pro = get_pro()
        intervals = build_intervals(db, pro)
        # 校验区间不重叠
        by_code: dict[str, list[dict]] = {}
        for it in intervals:
            by_code.setdefault(it["code"], []).append(it)
        overlaps = 0
        for code, segs in by_code.items():
            segs_sorted = sorted(segs, key=lambda x: x["effective_from"])
            for a, b in zip(segs_sorted, segs_sorted[1:]):
                if a["effective_to"] is None or a["effective_to"] >= b["effective_from"]:
                    overlaps += 1
        st_count = sum(1 for it in intervals if it["is_st"])
        print(
            f"codes={len(by_code)} intervals={len(intervals)} "
            f"st_intervals={st_count} overlaps={overlaps}"
        )
        if overlaps:
            print("ERROR: overlapping intervals, aborting")
            return
        if args.dry_run:
            for it in intervals[:5]:
                print("  sample:", it)
            st_samples = [it for it in intervals if it["is_st"]][:5]
            for it in st_samples:
                print("  st_sample:", it)
            print("dry-run, not writing")
            return

        # 写入：先清本 source_version，再批量插入（幂等）
        db.execute(
            text("DELETE FROM stock_metadata_history WHERE source_version = :sv"),
            {"sv": args.source_version},
        )
        objs = [
            StockMetadataHistory(
                code=it["code"],
                name=it["name"],
                industry=it["industry"],
                market=it["market"],
                list_date=it["list_date"],
                is_st=it["is_st"],
                effective_from=it["effective_from"],
                effective_to=it["effective_to"],
                source_version=args.source_version,
            )
            for it in intervals
        ]
        db.bulk_save_objects(objs)
        db.commit()
        print(f"wrote {len(objs)} intervals, source_version={args.source_version}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
