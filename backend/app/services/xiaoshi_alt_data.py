"""xiaoshi quant-data 回退：margin_trading → margin_detail，restricted_unlock → share_floats。

背景：Tushare 官方 margin_detail 40203 无权限（promax 回退时有缺日/半日），
share_float 频率超限（1 次/小时）导致事件快照 publish_blocked，share_floats 表停在 05-24。
xiaoshi 两个数据集是「最新快照」，按日调用一次（分页），不触碰报价/全市场行情接口，
也不触碰已 429 的 history/download-session。

写库约定：
- margin_detail：仅写 Tushare/promax 缺失的 (code, trade_date)，已有行不覆盖。
- share_floats：holder_name 以 ``xiaoshi:`` 前缀标识来源，按 (code, float_date, holder_name) 去重；
  float_ratio 统一成百分比口径（xiaoshi unlock_ratio 是小数）。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.tables import MarginDetail, ShareFloat

logger = logging.getLogger(__name__)

XIAOSHI_FLOAT_PREFIX = "xiaoshi:"
_PAGE = 1000
_MAX_PAGES = 10


def _paged(client: Any, dataset: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_MAX_PAGES):
        resp = client.get_quant_data(dataset, limit=_PAGE, offset=offset)
        items = resp.get("items") or resp.get("data") or resp.get("rows") or []
        rows.extend(i for i in items if isinstance(i, dict))
        total = int(resp.get("total_count") or 0)
        offset = int(resp.get("next_offset") or offset + len(items))
        # has_more 在越过 total_count 后仍可能为 True（实测会回绕），必须以 total_count 截止。
        if not items or not resp.get("has_more") or (total and offset >= total):
            break
    return rows


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def margin_rows_from_xiaoshi(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        code = str(it.get("code") or "")[:6]
        day = str(it.get("event_date") or "")[:10]
        if len(code) != 6 or not code.isdigit() or len(day) != 10:
            continue
        yi = lambda k: (None if _f(it.get(k)) is None else round(_f(it.get(k)) / 1e8, 6))  # noqa: E731
        out.append({
            "code": code,
            "trade_date": day,
            "rzye": yi("financing_balance"),
            "rqye": yi("securities_lending_balance"),
            "rzmre": yi("financing_buy"),
            "rzche": yi("financing_repayment"),
            "rzrqye": yi("margin_total"),
            "rqyl": None,
        })
    return out


def float_rows_from_xiaoshi(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        code = str(it.get("code") or "")[:6]
        day = str(it.get("event_date") or "")[:10]
        if len(code) != 6 or not code.isdigit() or len(day) != 10:
            continue
        ratio = _f(it.get("unlock_ratio"))
        out.append({
            "code": code,
            "ann_date": None,
            "float_date": day.replace("-", ""),  # 与表内既有 YYYYMMDD 口径一致
            "float_share": _f(it.get("unlock_shares")),
            "float_ratio": None if ratio is None else round(ratio * 100.0, 4),
            "holder_name": (XIAOSHI_FLOAT_PREFIX + str(it.get("unlock_type") or ""))[:120],
        })
    return out


def sync_xiaoshi_alt_data(db: Session, *, client: Any | None = None) -> dict[str, Any]:
    """每日一次：补 margin_detail 缺口 + 刷新 xiaoshi 解禁日历。异常不外抛。"""
    result: dict[str, Any] = {"margin_inserted": 0, "float_inserted": 0, "errors": []}
    try:
        if client is None:
            from app.services.xiaoshi_client import get_xiaoshi_client
            client = get_xiaoshi_client()
    except Exception as exc:  # 未配置 key 等
        result["errors"].append(f"client: {str(exc)[:120]}")
        return result

    try:
        margin = margin_rows_from_xiaoshi(_paged(client, "margin_trading"))
        days = sorted({r["trade_date"] for r in margin})
        existing: set[tuple[str, str]] = set()
        if days:
            existing = {
                (c, d) for c, d in db.execute(
                    select(MarginDetail.code, MarginDetail.trade_date).where(MarginDetail.trade_date.in_(days))
                ).all()
            }
        new = []
        for r in margin:
            key = (r["code"], r["trade_date"])
            if key in existing:
                continue
            existing.add(key)
            new.append(r)
        if new:
            db.bulk_insert_mappings(MarginDetail, new)
            db.commit()
        result.update(margin_inserted=len(new), margin_days=days, margin_rows=len(margin))
    except Exception as exc:
        db.rollback()
        logger.warning("xiaoshi margin_trading fallback failed: %s", exc)
        result["errors"].append(f"margin: {str(exc)[:120]}")

    try:
        floats = float_rows_from_xiaoshi(_paged(client, "restricted_unlock"))
        existing_f = {
            (c, d, h) for c, d, h in db.execute(text(
                "SELECT code, float_date, holder_name FROM share_floats WHERE holder_name LIKE :p"
            ), {"p": XIAOSHI_FLOAT_PREFIX + "%"}).all()
        }
        new_f = []
        for r in floats:
            key = (r["code"], r["float_date"], r["holder_name"])
            if key in existing_f:
                continue
            existing_f.add(key)
            new_f.append(r)
        if new_f:
            db.bulk_insert_mappings(ShareFloat, new_f)
            db.commit()
        result.update(float_inserted=len(new_f), float_rows=len(floats))
    except Exception as exc:
        db.rollback()
        logger.warning("xiaoshi restricted_unlock fallback failed: %s", exc)
        result["errors"].append(f"float: {str(exc)[:120]}")
    return result


def xiaoshi_upcoming_floats(
    db: Session, codes: list[str], trade_date: str, *, days: int = 30, as_of: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """share_float 事件快照不可用时的读回退（只读本地表，不发请求）。

    PIT：只用 created_at <= as_of 当日结束 的 xiaoshi 行，回测不会看到未来采集的数据。
    """
    from datetime import date, timedelta

    if not codes:
        return {}
    start = date.fromisoformat(trade_date[:10])
    end = (start + timedelta(days=days)).isoformat()
    cutoff = f"{(as_of or trade_date)[:10]} 23:59:59"
    rows = db.execute(
        select(ShareFloat).where(
            ShareFloat.code.in_(list(codes)),
            ShareFloat.holder_name.like(XIAOSHI_FLOAT_PREFIX + "%"),
            ShareFloat.float_date >= start.isoformat().replace("-", ""),
            ShareFloat.float_date <= end.replace("-", ""),
            ShareFloat.created_at <= cutoff,
        )
    ).scalars().all()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        fd = str(r.float_date or "")
        out.setdefault(r.code, []).append({
            "ann_date": r.ann_date,
            "float_date": f"{fd[:4]}-{fd[4:6]}-{fd[6:8]}" if len(fd) == 8 else fd,
            "float_share": r.float_share,
            "float_ratio": r.float_ratio,
            "holder_name": r.holder_name,
            "share_type": None,
        })
    return out
