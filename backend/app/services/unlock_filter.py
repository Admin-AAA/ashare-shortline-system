"""§99 限售解禁回避：未来 N 个交易日内解禁占总股本 %（share_floats，quantdata 行，严格 PIT）。

口径（scripts/midterm/lt_research17.py / scripts/research_unlock_entry_filter.py 共用）：
  - 只用 quantdata 行（ann_date 非空、holder_name 不以 'xiaoshi:' 开头；xiaoshi 日历行口径不同且与其重叠）；
  - 同一笔 (code, float_date, holder, round(float_share)) 在多个公告里出现时取最早 ann_date、去重；
  - 窗口：float_date ∈ (T, T 之后第 N 个交易日]；PIT：只计 ann_date ≤ T 的行；
  - float_ratio 为占总股本 %，按 (code, float_date) 求和。
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import bindparam, text

# (float_date, ann_date, ratio%)，均为 YYYYMMDD 字符串
Events = dict[str, list[tuple[str, str, float]]]

_SQL = """
SELECT code, float_date, ann, SUM(r) AS r FROM (
  SELECT code, float_date, MIN(ann_date) AS ann, MAX(float_ratio) AS r
  FROM share_floats
  WHERE ann_date IS NOT NULL AND ann_date <> ''
    AND (holder_name IS NULL OR holder_name NOT LIKE 'xiaoshi:%%')
    AND float_date > :fd0 AND float_date <= :fd1 {codes}
  GROUP BY code, float_date, holder_key, float_share_key
) x GROUP BY code, float_date, ann
"""


def _ymd(d: str) -> str:
    return str(d).replace("-", "")[:8]


def load_events(db, fd_from: str, fd_to: str, codes: Iterable[str] | None = None) -> Events:
    """解禁事件（去重后按 (code, float_date, ann_date) 汇总）。fd_from 不含、fd_to 含。"""
    codes = sorted(set(codes)) if codes is not None else None
    if codes is not None and not codes:
        return {}
    sql = _SQL.format(codes="AND code IN :codes" if codes is not None else "").replace("%%", "%")
    q = text(sql)
    params: dict[str, Any] = {"fd0": _ymd(fd_from), "fd1": _ymd(fd_to)}
    if codes is not None:
        q = q.bindparams(bindparam("codes", expanding=True))
        params["codes"] = codes
    out: Events = defaultdict(list)
    for code, fd, ann, r in db.execute(q, params):
        out[str(code)].append((str(fd), str(ann), float(r or 0.0)))
    return dict(out)


def ratio_between(events: Events, code: str, t: str, end: str, pit: bool = True) -> float:
    """float_date ∈ (t, end] 的解禁合计（占总股本 %）；pit=True 时只计 ann_date ≤ t。"""
    t, end = _ymd(t), _ymd(end)
    return sum(r for fd, ann, r in events.get(code, ()) if t < fd <= end and (not pit or ann <= t))


def nth_trading_day(db, as_of: str, n: int) -> str:
    """as_of 之后第 n 个交易日（YYYYMMDD）；trade_calendar 不够长时用工作日近似补足。"""
    t = _ymd(as_of)
    cal_min = db.execute(text("SELECT MIN(cal_date) FROM trade_calendar WHERE exchange='SSE' AND is_open=1")).scalar()
    if cal_min and t >= str(cal_min):
        days = [str(r[0]) for r in db.execute(text(
            "SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>:d "
            "ORDER BY cal_date LIMIT :n"), {"d": t, "n": int(n)})]
    else:  # trade_calendar 只覆盖近端：历史日期用 daily_kbars 的交易日
        iso = f"{t[:4]}-{t[4:6]}-{t[6:]}"
        days = [str(r[0]).replace("-", "") for r in db.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date>:d "
            "ORDER BY trade_date LIMIT :n"), {"d": iso, "n": int(n)})]
    if len(days) >= n:
        return days[n - 1]
    d = _dt.datetime.strptime(days[-1] if days else t, "%Y%m%d").date()
    k = len(days)
    while k < n:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            k += 1
    return d.strftime("%Y%m%d")


def unlock_ratios(db, codes: Iterable[str], as_of: str, n_days: int, pit: bool = True) -> dict[str, float]:
    codes = sorted(set(codes))
    if not codes:
        return {}
    end = nth_trading_day(db, as_of, n_days)
    ev = load_events(db, as_of, end, codes)
    return {c: ratio_between(ev, c, as_of, end, pit) for c in codes}


def blocked_codes(db, codes: Iterable[str], as_of: str, n_days: int, threshold_pct: float) -> set[str]:
    """未来 n_days 个交易日内（PIT）解禁合计 ≥ threshold_pct% 总股本的代码。"""
    return {c for c, r in unlock_ratios(db, codes, as_of, n_days).items() if r >= threshold_pct - 1e-9}
