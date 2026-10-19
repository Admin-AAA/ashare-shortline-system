"""[中线] §109 股权激励草案（含股票期权）公告后漂移（mid-incentive-option）实盘候选。

研究：scripts/midterm/lt_research23.py INC_opt_h20（预注册 3 版中采纳的一版，未调参）。
数据：巨潮资讯公开检索（与 app/services/cninfo_news.py 同一 hisAnnouncement/query 接口，只读），
category_gqjl_szsh（股权激励）+ searchkey「草案」，全市场。经统一数据网关 ann.search.cninfo（app.services.datasource.cninfo_search）。
口径：标题含「激励计划」且含「草案」、不含 修订/更正/调整/终止/撤销/取消/延期；日期 = announcementTime 的北京时间日期
（巨潮多把晚间公告标为次日 00:00）；按 (股票, 日期) 聚合，首次 = 该股前 180 自然日内无同类公告；
当日任一标题含「股票期权」才入选（INC_opt）。公告日在 (上一交易日, as_of]，as_of 收盘出信号、次日开盘买、持 20 日。
排序分 = 信号日前 20 日复权涨幅 r20 升序（优先尚未提前大涨的），平手按代码升序。流动性：midterm_service._liq_ok。
"首次"判断：历史 output/research/sprint4/incentive/*.parquet（研究缓存 2018-01~2026-10-16）
+ 实盘追加库 data/midterm/events/incentive_live_<year>.parquet（本模块追加）。"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
HIST_DIR = ROOT / "output" / "research" / "sprint4" / "incentive"
LIVE_DIR = ROOT / "data" / "midterm" / "events"
BAD = re.compile("修订|更正|调整|终止|撤销|取消|延期")
FIRST_DAYS = 180
COLS = ["code", "name", "ann_ms", "ann_date", "title", "ann_id"]
_TZ = _dt.timezone(_dt.timedelta(hours=8))


def is_draft(title: str) -> bool:
    return "激励计划" in title and "草案" in title and not BAD.search(title)


def fetch_range(start: str, end: str, max_pages: int = 60) -> list[dict[str, Any]] | None:
    """巨潮按披露日期区间（YYYY-MM-DD）拉全市场股权激励「草案」公告（经数据网关 ann.search.cninfo）；失败返回 None。"""
    from app.services.datasource.cninfo_search import search_announcements
    rows: list[dict[str, Any]] = []
    try:
        ann = search_announcements(searchkey="草案", category="category_gqjl_szsh", start=start, end=end,
                                   column="szse", max_pages=max_pages, page_size=30)
        for x in ann:
            raw = x.get("raw") or {}
            ms = raw.get("announcementTime")
            if ms is None:
                t = x.get("announcementTime")
                ms = int(t.timestamp() * 1000) if isinstance(t, _dt.datetime) else 0
            ms = int(ms or 0)
            rows.append({"code": str(x.get("secCode") or ""), "name": str(x.get("secName") or ""), "ann_ms": ms,
                         "ann_date": _dt.datetime.fromtimestamp(ms / 1000, _TZ).strftime("%Y%m%d"),
                         "title": re.sub(r"</?em>", "", str(x.get("announcementTitle") or "")),
                         "ann_id": str(x.get("announcementId") or "")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("cninfo incentive %s~%s failed: %s", start, end, exc)
        return None
    return rows


def _store_live(rows: list[dict[str, Any]]) -> None:
    import pandas as pd
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        new = pd.DataFrame(rows, columns=COLS)
        for year, part in new.groupby(new["ann_date"].str.slice(0, 4)):
            f = LIVE_DIR / f"incentive_live_{year}.parquet"
            merged = pd.concat([pd.read_parquet(f), part]) if f.exists() else part
            merged.drop_duplicates("ann_id").to_parquet(f, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("store incentive live events failed: %s", exc)


def _history_dates(codes: list[str], before: str) -> dict[str, str]:
    """各股票 before（YYYYMMDD）之前最近一次草案公告日期。"""
    import pandas as pd
    parts = []
    for f in sorted(HIST_DIR.glob("*.parquet")) + sorted(LIVE_DIR.glob("incentive_live_*.parquet")):
        try:
            x = pd.read_parquet(f, columns=["code", "ann_date", "title"])
            parts.append(x[x["code"].astype(str).isin(codes) & (x["ann_date"].astype(str) < before)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("incentive history %s read failed: %s", f.name, exc)
    if not parts:
        return {}
    h = pd.concat(parts, ignore_index=True)
    h = h[h["title"].astype(str).map(is_draft)]
    return h.groupby("code")["ann_date"].max().astype(str).to_dict() if len(h) else {}


def first_events(rows: list[dict[str, Any]], start: str, end: str, prior: dict[str, str]) -> dict[str, dict[str, Any]]:
    """rows（巨潮行）-> {code: 事件}：ann_date∈[start,end]（YYYYMMDD）的首次草案（前 180 天无同类，含区间内更早的）。"""
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        code, day, title = str(r["code"]), str(r["ann_date"]), str(r["title"])
        if not re.fullmatch(r"\d{6}", code) or not is_draft(title):
            continue
        e = by.setdefault((code, day), {"code": code, "ann_date": day, "opt": False, "titles": []})
        e["opt"] = e["opt"] or ("股票期权" in title)
        e["titles"].append(title)
    out: dict[str, dict[str, Any]] = {}
    last = dict(prior)
    for (code, day), e in sorted(by.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        p = last.get(code)
        last[code] = day
        if p is not None:
            gap = (_dt.datetime.strptime(day, "%Y%m%d") - _dt.datetime.strptime(p, "%Y%m%d")).days
            if gap <= FIRST_DAYS:
                continue
        if start <= day <= end and code not in out:
            out[code] = e
    return out


def _r20(db, as_of: str, dates: list[str], codes: list[str]) -> dict[str, float]:
    """信号日前 20 日复权涨幅（daily_kbars pct_chg 近 20 行累乘，±35% 截断，同 lt_research.build r20）。"""
    rows = db.execute(text(
        "SELECT code, pct_chg FROM daily_kbars WHERE trade_date>:d20 AND trade_date<=:d AND code IN :codes "
        "ORDER BY code, trade_date").bindparams(bindparam("codes", expanding=True)),
        {"d20": dates[20], "d": as_of, "codes": codes}).all()
    acc: dict[str, list[float]] = {}
    for code, pc in rows:
        acc.setdefault(code, []).append(float(pc or 0.0))
    out = {}
    for code, xs in acc.items():
        v = 1.0
        for x in xs:
            v *= 1 + max(-35.0, min(35.0, x)) / 100
        out[code] = v - 1
    return out


def incentive_candidates(db, as_of: str, store: bool = True, opt_only: bool = True) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的首次股权激励草案（含股票期权）信号（lt_research23 INC_opt_h20）。数据拉取失败返回 None。"""
    from app.services.midterm_service import _liq_ok
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    d0 = _dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)
    start, end = d0.strftime("%Y%m%d"), as_of.replace("-", "")
    # 往前多拉 1 天：巨潮晚间公告的日期标注偶有跨日，区间内重复按 ann_id 去重，归属仍按 ann_date 过滤
    rows = fetch_range((d0 - _dt.timedelta(days=1)).isoformat(), as_of)
    if rows is None:
        return None
    if store and rows:
        _store_live(rows)
    codes = sorted({str(r["code"]) for r in rows if is_draft(str(r["title"]))})
    if not codes:
        return []
    ev = first_events(rows, start, end, _history_dates(codes, start))
    if opt_only:
        ev = {k: v for k, v in ev.items() if v["opt"]}
    ev = {k: v for k, v in ev.items() if k[0] not in "489"}
    if not ev:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(ev))
    if not ok:
        return []
    r20 = _r20(db, as_of, dates, sorted(ok))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok)}).all())
    res = [{"code": c, "name": names.get(c, ""), "ann_date": ev[c]["ann_date"], "opt": ev[c]["opt"],
            "title": ev[c]["titles"][0][:120], "r20": round(r20.get(c, 0.0), 4), "score": round(-r20.get(c, 0.0), 6),
            "total_mv": float(ok[c]["total_mv"] or 0), "amt20": float(ok[c]["amt20"])} for c in ok]
    return sorted(res, key=lambda x: (x["r20"], x["code"]))
