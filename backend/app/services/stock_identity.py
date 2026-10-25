"""股票身份数据（stocks / stock_metadata_history）的每日增量刷新与三方对账。

背景（2026-10-25 审计 P0-1）：
- stock_metadata_history 曾由 scripts/backfill_stock_metadata_history.py 在 2026-08-10 一次性生成，
  之后没人刷新；stocks 表也只靠推荐流水线顺带 upsert，10-11 后停更。结果新股没有身份、
  ST 摘帽/戴帽不更新、st_hist.parquet 与回测的 ST 过滤失真。
- 现在统一由本模块维护：
  1. ``refresh_stock_identity``：stock_basic(L/D) + namechange 经数据网关取数（走限流 / 熔断 / 路由），
     upsert stocks，再重建全部元数据区间并与库中现有区间做 diff，只写差异（INSERT/UPDATE/DELETE），
     可 dry-run；
  2. ``reconcile_stock_identity``：stocks / metadata / daily_kbars 活跃代码三方对账，只读，
     结果接入 data_freshness（emit_alert("data_freshness", ...)）。

ST 状态口径：以名称含 ST 为准（namechange 每一行的 name、stock_basic 当前 name），
而不是 stocks.is_st（历史上它与名称不一致）。退市整理期（reason）也计 ST。
区间不重叠（stock_metadata.resolve_stock_metadata_as_of 硬约束）；effective_from 一律 ISO 'YYYY-MM-DD'
（字符串比较 'YYYYMMDD' 会整体大于任何 ISO 日期，使该行永远不可见）。
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

SOURCE_VERSION = "stocks+namechange/rolling/v2"
WINDOW_START = "2015-01-01"
NAMECHANGE_START_YEAR = 2015

# 与 stocks 现有行数比，stock_basic(L) 少于该比例视为截断，拒绝写库
MIN_LISTED_RATIO = 0.9
MIN_LISTED_ABS = 4500

_ST_ENTER_REASONS = {"ST", "*ST", "叠加ST", "叠加*ST", "从ST变为*ST", "退市整理期", "撤消*ST并实行ST"}
_ST_EXIT_REASONS = {"撤销ST", "撤销*ST", "撤销叠加ST", "撤销叠加*ST"}
_CST = timezone(timedelta(hours=8))
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# 上游频控很紧（promax namechange 1 次/小时，stock_basic 同样偏紧；quantdata 有日配额），
# 所以已结束年份的 namechange 和退市名单落本地缓存，日常刷新只请求「当年 namechange + 上市名单」。
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "runtime" / "stock_identity"
DELISTED_CACHE_TTL_S = 7 * 86400


class StockIdentityError(RuntimeError):
    """身份数据源返回异常（空 / 截断），本次刷新放弃，库保持不变。"""


# ── 纯函数 ────────────────────────────────────────────────────────────────

def is_st_name(name: Any) -> bool:
    """名称含 ST（*ST / ST / S*ST …），或处于退市整理期（名称以「退」结尾 / 开头，如 萃华退、退市新亿）。"""
    n = str(name or "").strip()
    return "ST" in n.upper() or n.endswith("退") or n.endswith("退)") or n.startswith("退")


def _today() -> str:
    return datetime.now(_CST).date().isoformat()


def iso_date(value: Any) -> str | None:
    """'YYYYMMDD' / 'YYYY-MM-DD' / date → ISO；无法识别返回 None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s or s.lower() in {"none", "nan", "nat"}:
        return None
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10] if _ISO_RE.match(s[:10]) else None


def normalize_code(ts_code: Any) -> str:
    return str(ts_code).split(".")[0].strip()


def is_valid_code(code: str) -> bool:
    """标准 6 位数字股票代码（过滤 T600018 这类异常前缀）。"""
    return len(code) == 6 and code.isdigit()


def _prev_day(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def derive_events(namechange: pd.DataFrame) -> dict[str, list[tuple[str, bool, str]]]:
    """namechange → {code: [(start_iso, is_st, reason)]}，每一行名称变更都是一个状态点。"""
    events: dict[str, list[tuple[str, bool, str]]] = {}
    if namechange is None or len(namechange) == 0:
        return events
    for row in namechange.to_dict("records"):
        code = normalize_code(row.get("ts_code"))
        start = iso_date(row.get("start_date"))
        if not is_valid_code(code) or not start:
            continue
        reason = str(row.get("change_reason") or "")
        st = is_st_name(row.get("name")) or reason in _ST_ENTER_REASONS
        if reason in _ST_EXIT_REASONS and not is_st_name(row.get("name")):
            st = False
        events.setdefault(code, []).append((start, st, reason))
    return events


def build_intervals(
    current: dict[str, dict[str, Any]],
    events: dict[str, list[tuple[str, bool, str]]],
    *,
    today: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """为每只股票构建不重叠的元数据区间。

    current: code → {name, industry, market, list_date(iso|None), is_st(bool), delist_date(iso|None, 可缺)}。
    events:  derive_events 的输出。
    返回 (区间列表, 统计)。区间字段：code,name,industry,market,list_date,is_st,effective_from,effective_to。
    """
    today = today or _today()
    stats = {"codes": 0, "sync_points": 0, "skipped_before_window": 0}
    intervals: list[dict[str, Any]] = []
    for code, meta in current.items():
        if not is_valid_code(code):
            continue
        eff_from = max(meta.get("list_date") or WINDOW_START, WINDOW_START)
        delist_to = meta.get("delist_date")
        if delist_to and delist_to < eff_from:
            stats["skipped_before_window"] += 1  # 退市早于窗口起点：窗口内无有效元数据
            continue
        cur_st = bool(meta["is_st"])
        evs = sorted({(pt, st) for pt, st, _ in events.get(code, [])})
        reasons_by_pt = {pt: rs for pt, _, rs in events.get(code, [])}

        if not evs:
            points = [(eff_from, cur_st)]
        else:
            # eff_from 当天（含）之前的最后一个事件决定初始状态；否则看第一个事件：
            # 明确的「撤销ST」说明此前是 ST，其余情形此前非 ST。
            before = [(pt, st) for pt, st in evs if pt <= eff_from]
            after = [(pt, st) for pt, st in evs if pt > eff_from]
            if before:
                init = before[-1][1]
            elif after:
                first_pt, first_st = after[0]
                init = reasons_by_pt.get(first_pt, "") in _ST_EXIT_REASONS and not first_st
            else:
                init = cur_st
            points = [(eff_from, init)] + after
            # stock_basic 当前名称是「现状」的权威：事件序列末态与之不符（namechange 滞后）时，
            # 在今天补一个校正点，namechange 日后补齐会在下次刷新中自动取代它。
            if points[-1][1] != cur_st and (not delist_to or today <= delist_to):
                points.append((max(today, points[-1][0]), cur_st))
                stats["sync_points"] += 1
        # 相邻同状态合并；同一起点保留最后一个
        collapsed: list[tuple[str, bool]] = []
        for pt, st in points:
            if collapsed and collapsed[-1][0] == pt:
                collapsed[-1] = (pt, st)
            elif collapsed and collapsed[-1][1] == st:
                continue
            else:
                collapsed.append((pt, st))
        for idx, (pt, st) in enumerate(collapsed):
            if delist_to and pt > delist_to:
                continue
            nxt = collapsed[idx + 1][0] if idx + 1 < len(collapsed) else None
            seg_to = _prev_day(nxt) if nxt else delist_to
            if delist_to and (seg_to is None or seg_to > delist_to):
                seg_to = delist_to
            intervals.append({
                "code": code, "name": meta["name"], "industry": meta.get("industry"),
                "market": meta.get("market"), "list_date": meta.get("list_date"),
                "is_st": st, "effective_from": pt, "effective_to": seg_to,
            })
        stats["codes"] += 1
    return intervals, stats


def count_overlaps(intervals: list[dict[str, Any]]) -> int:
    by_code: dict[str, list[dict[str, Any]]] = {}
    for it in intervals:
        by_code.setdefault(it["code"], []).append(it)
    overlaps = 0
    for segs in by_code.values():
        segs.sort(key=lambda x: x["effective_from"])
        for a, b in zip(segs, segs[1:]):
            if a["effective_to"] is None or a["effective_to"] >= b["effective_from"]:
                overlaps += 1
    return overlaps


_COMPARE_FIELDS = ("name", "industry", "market", "list_date", "is_st", "effective_to", "source_version")


def diff_intervals(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
    *,
    source_version: str = SOURCE_VERSION,
    scope_codes: set[str] | None = None,
) -> dict[str, list]:
    """库中现有区间 vs 新区间，按 (code, effective_from) 配对。

    scope_codes：只处理这些代码（取数失败的部分不动其它代码）。
    返回 {insert: [区间], update: [(id, 新区间, 变化字段)], delete: [id], normalize: n}。
    existing 里 effective_from 为 'YYYYMMDD' 的行先归一成 ISO 再配对，字段 effective_from 的修正计入 update。
    """
    new_by_key = {(it["code"], it["effective_from"]): {**it, "source_version": source_version} for it in new}
    existing_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    delete: list[int] = []
    normalized = 0
    for row in sorted(existing, key=lambda r: int(r["id"])):
        code = str(row["code"])
        if scope_codes is not None and code not in scope_codes:
            continue
        ef = iso_date(row["effective_from"]) or str(row["effective_from"])
        key = (code, ef)
        if key in existing_by_key:  # 归一后撞键：保留 id 小的，多余的删
            delete.append(int(row["id"]))
            continue
        fixed = dict(row)
        if ef != str(row["effective_from"]):
            normalized += 1
        fixed["effective_from_orig"] = str(row["effective_from"])
        fixed["effective_from"] = ef
        fixed["effective_to"] = iso_date(row.get("effective_to"))
        fixed["list_date"] = iso_date(row.get("list_date")) or row.get("list_date")
        existing_by_key[key] = fixed
    insert: list[dict[str, Any]] = []
    update: list[tuple[int, dict[str, Any], list[str]]] = []
    for key, it in new_by_key.items():
        old = existing_by_key.get(key)
        if old is None:
            insert.append(it)
            continue
        changed = [f for f in _COMPARE_FIELDS if _norm(old.get(f)) != _norm(it.get(f))]
        if old["effective_from_orig"] != key[1]:
            changed.append("effective_from")
        if changed:
            update.append((int(old["id"]), it, changed))
    for key, old in existing_by_key.items():
        if key not in new_by_key:
            delete.append(int(old["id"]))
    return {"insert": insert, "update": update, "delete": delete, "normalized": normalized}


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else float(v)
    return str(v)


# ── 取数（走数据网关：get_pro 内部按 routing_table 路由 + 限流 + 熔断） ────────────

def fetch_listed(pro: Any) -> pd.DataFrame:
    df = pro.stock_basic(exchange="", list_status="L",
                         fields="ts_code,symbol,name,area,industry,market,list_date")
    return df if df is not None else pd.DataFrame()


def fetch_delisted(pro: Any, cache_dir: Path | None = CACHE_DIR) -> pd.DataFrame:
    """退市名单：变化极慢，本地缓存 7 天。"""
    path = cache_dir / "delisted.parquet" if cache_dir else None
    if path is not None and path.exists() and time.time() - path.stat().st_mtime < DELISTED_CACHE_TTL_S:
        return pd.read_parquet(path)
    df = pro.stock_basic(exchange="", list_status="D",
                         fields="ts_code,name,industry,market,list_date,delist_date")
    df = df if df is not None else pd.DataFrame()
    if path is not None and len(df) > 0:
        _write_cache(df, path)
    return df


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.astype(object).where(df.notna(), None).to_parquet(tmp)
    tmp.replace(path)


def fetch_namechange(
    pro: Any, start_year: int, end_year: int, cache_dir: Path | None = CACHE_DIR
) -> tuple[pd.DataFrame, dict[int, int]]:
    """逐年取 namechange。早于 end_year 的年份命中本地缓存就不再请求；end_year（当年）每次都请求。"""
    frames, per_year = [], {}
    for yr in range(start_year, end_year + 1):
        path = cache_dir / f"namechange_{yr}.parquet" if cache_dir else None
        if yr < end_year and path is not None and path.exists():
            df = pd.read_parquet(path)
        else:
            df = pro.namechange(start_date=f"{yr}0101", end_date=f"{yr}1231")
            if path is not None and df is not None and yr < end_year and len(df) > 0:
                _write_cache(df, path)  # 已结束年份：落缓存
        per_year[yr] = 0 if df is None else len(df)
        if df is not None and len(df) > 0:
            frames.append(df)
    if not frames:
        return pd.DataFrame(), per_year
    return pd.concat(frames, ignore_index=True).drop_duplicates(), per_year


def _listed_to_current(listed: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in listed.to_dict("records"):
        code = normalize_code(row.get("symbol") or row.get("ts_code"))
        if not is_valid_code(code):
            continue
        name = str(row.get("name") or "").strip()
        ind = str(row.get("industry") or "").strip()
        out[code] = {
            "name": name,
            "industry": ind if ind and ind.lower() != "none" else None,
            "area": (str(row.get("area") or "").strip() or None),
            "market": (str(row.get("market") or "").strip() or None),
            "list_date": iso_date(row.get("list_date")),
            "is_st": is_st_name(name),
        }
    return out


def _delisted_to_current(delisted: pd.DataFrame, listed_codes: set[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in delisted.to_dict("records"):
        code = normalize_code(row.get("ts_code"))
        if not is_valid_code(code) or code in listed_codes:
            continue
        ind = str(row.get("industry") or "").strip()
        market = str(row.get("market") or "").strip()
        out[code] = {
            "name": str(row.get("name") or "").strip(),
            "industry": ind if ind and ind.lower() != "none" else None,
            "market": market[:2] if market else None,
            "list_date": iso_date(row.get("list_date")),
            "is_st": is_st_name(row.get("name")),
            "delist_date": iso_date(row.get("delist_date")),
        }
    return out


# ── 刷新 ──────────────────────────────────────────────────────────────────

def _load_existing_intervals(db) -> list[dict[str, Any]]:
    rows = db.execute(text(
        "SELECT id, code, name, industry, market, list_date, is_st, effective_from, effective_to, source_version "
        "FROM stock_metadata_history")).mappings().all()
    return [dict(r) for r in rows]


def refresh_stock_identity(
    db, pro: Any = None, *, dry_run: bool = False, today: str | None = None, cache_dir: Path | None = CACHE_DIR
) -> dict[str, Any]:
    """拉取 stock_basic / namechange，增量刷新 stocks 与 stock_metadata_history。幂等。

    失败语义：stock_basic(L) 为空或明显截断 → StockIdentityError，库不动；
    stock_basic(D)/namechange 取数失败 → 对应部分不动（保留库中已有区间），结果里带 warnings。
    """
    if pro is None:
        from app.services.tushare_client import get_pro
        pro = get_pro()
    today = today or _today()
    warnings: list[str] = []

    listed = fetch_listed(pro)
    current = _listed_to_current(listed)
    n_stocks = int(db.execute(text("SELECT COUNT(*) FROM stocks")).scalar() or 0)
    if len(current) < max(MIN_LISTED_ABS, int(n_stocks * MIN_LISTED_RATIO)):
        raise StockIdentityError(f"stock_basic(L) 仅返回 {len(current)} 行（库中 stocks={n_stocks}），疑似空/截断，放弃刷新")

    delist_ok = True
    try:
        delisted = _delisted_to_current(fetch_delisted(pro, cache_dir), set(current))
    except Exception as exc:  # noqa: BLE001 - 退市名单失败不应阻断上市股刷新
        delist_ok = False
        delisted = {}
        warnings.append(f"stock_basic(D) 取数失败，退市股区间保持不动: {type(exc).__name__}: {str(exc)[:120]}")

    nc_ok = True
    per_year: dict[int, int] = {}
    try:
        nc, per_year = fetch_namechange(pro, NAMECHANGE_START_YEAR, int(today[:4]), cache_dir)
        if len(nc) == 0:
            raise StockIdentityError("namechange 返回空")
    except Exception as exc:  # noqa: BLE001
        nc_ok = False
        nc = pd.DataFrame()
        warnings.append(f"namechange 取数失败，ST 区间保持不动: {type(exc).__name__}: {str(exc)[:120]}")

    result: dict[str, Any] = {
        "dry_run": dry_run, "listed": len(current), "delisted": len(delisted), "namechange_rows": len(nc),
        "namechange_per_year": per_year, "warnings": warnings,
    }

    # 1) stocks：upsert 上市股。已有行只更新 name/industry/list_date/is_st/area（industry/area/list_date 只在新值非空时覆盖），
    #    market 只在新增行或原值为空时写，避免和流水线写入的 SH/SZ 口径互相打架。
    existing_stocks = {
        str(r[0]): r for r in db.execute(text("SELECT code, name, industry, area, market, list_date, is_st FROM stocks")).all()
    }
    stock_insert, stock_update = [], []
    for code, m in current.items():
        old = existing_stocks.get(code)
        if old is None:
            stock_insert.append({"code": code, "name": m["name"], "industry": m["industry"], "area": m["area"],
                                 "market": m["market"], "list_date": m["list_date"], "is_st": int(m["is_st"])})
            continue
        new = {
            "code": code,
            "name": m["name"] or old[1],
            "industry": m["industry"] or old[2],
            "area": m["area"] or old[3],
            "market": old[4] or m["market"],
            "list_date": m["list_date"] or old[5],
            "is_st": int(m["is_st"]),
        }
        if any(_norm(new[k]) != _norm(v) for k, v in zip(("name", "industry", "area", "market", "list_date", "is_st"), old[1:])):
            stock_update.append(new)
    # 已退市但仍留在 stocks 里的代码：名称 / is_st 跟 stock_basic(D) 对齐（否则 stocks 里永远停在退市前的 *ST 名）
    for code, m in delisted.items():
        old = existing_stocks.get(code)
        if old is None:
            continue
        new = {"code": code, "name": m["name"] or old[1], "industry": old[2], "area": old[3], "market": old[4],
               "list_date": m["list_date"] or old[5], "is_st": int(m["is_st"])}
        if any(_norm(new[k]) != _norm(v) for k, v in zip(("name", "industry", "area", "market", "list_date", "is_st"), old[1:])):
            stock_update.append(new)
    result["stocks"] = {"insert": len(stock_insert), "update": len(stock_update), "unchanged": len(current) - len(stock_insert) - len(stock_update),
                        "new_codes_sample": [r["code"] for r in stock_insert[:10]]}

    # 2) 区间：全量重建后与库 diff，只写差异。
    existing = _load_existing_intervals(db)
    # 区间里的 market 沿用 stocks 现值（流水线写的 SH/SZ/主板… 混合口径），不因本次刷新整体改口径
    merged_listed = {
        code: ({**m, "market": (existing_stocks[code][4] or m["market"])} if code in existing_stocks else m)
        for code, m in current.items()
    }
    all_current = {**delisted, **merged_listed}
    events = derive_events(nc)
    new_intervals, build_stats = build_intervals(all_current, events, today=today)
    overlaps = count_overlaps(new_intervals)
    if overlaps:
        raise StockIdentityError(f"重建的区间出现 {overlaps} 处重叠，放弃写入")
    scope = set(current)
    if delist_ok:
        scope |= set(delisted)
    if not nc_ok:
        # 没有 namechange：只补充库里还没有任何区间的新股（用当前态），不改已有代码
        have = {str(r["code"]) for r in existing}
        scope = {c for c in scope if c not in have}
    diff = diff_intervals(existing, new_intervals, scope_codes=scope)
    result["intervals"] = {
        "built": len(new_intervals), "build_stats": build_stats,
        "insert": len(diff["insert"]), "update": len(diff["update"]), "delete": len(diff["delete"]),
        "effective_from_normalized": diff["normalized"],
        "update_fields": _count_fields(diff["update"]),
        "st_intervals_after": sum(1 for it in new_intervals if it["is_st"]),
        "st_codes_after": len({it["code"] for it in new_intervals if it["is_st"]}),
    }
    result["st_changes_sample"] = [
        {"code": it["code"], "from": it["effective_from"], "to": it["effective_to"], "is_st": it["is_st"], "name": it["name"]}
        for it in diff["insert"] if it["is_st"]][:15]

    if dry_run:
        return result

    try:
        if stock_insert:
            db.execute(text(
                "INSERT INTO stocks (code, name, industry, area, market, list_date, is_st, updated_at) "
                "VALUES (:code, :name, :industry, :area, :market, :list_date, :is_st, UTC_TIMESTAMP())"), stock_insert)
        if stock_update:
            db.execute(text(
                "UPDATE stocks SET name=:name, industry=:industry, area=:area, market=:market, "
                "list_date=:list_date, is_st=:is_st, updated_at=UTC_TIMESTAMP() WHERE code=:code"), stock_update)
        if diff["delete"]:
            for i in range(0, len(diff["delete"]), 500):
                chunk = diff["delete"][i:i + 500]
                db.execute(text("DELETE FROM stock_metadata_history WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)), {"ids": chunk})
        if diff["update"]:
            db.execute(text(
                "UPDATE stock_metadata_history SET name=:name, industry=:industry, market=:market, list_date=:list_date, "
                "is_st=:is_st, effective_from=:effective_from, effective_to=:effective_to, source_version=:source_version "
                "WHERE id=:id"),
                [{**{k: it[k] for k in ("name", "industry", "market", "list_date", "is_st", "effective_from", "effective_to", "source_version")},
                  "is_st": int(it["is_st"]), "id": rid} for rid, it, _ in diff["update"]])
        if diff["insert"]:
            db.execute(text(
                "INSERT INTO stock_metadata_history (code, name, industry, market, list_date, is_st, effective_from, "
                "effective_to, source_version, created_at) VALUES (:code, :name, :industry, :market, :list_date, :is_st, "
                ":effective_from, :effective_to, :source_version, UTC_TIMESTAMP())"),
                [{**{k: it[k] for k in ("code", "name", "industry", "market", "list_date", "effective_from", "effective_to", "source_version")},
                  "is_st": int(it["is_st"])} for it in diff["insert"]])
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("stock_identity 刷新完成: %s", {k: result[k] for k in ("listed", "delisted", "stocks", "intervals")})
    return result


def _count_fields(updates: list[tuple[int, dict[str, Any], list[str]]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, _, fields in updates:
        for f in fields:
            out[f] = out.get(f, 0) + 1
    return out


# ── 三方对账（只读） ────────────────────────────────────────────────────────

def reconcile_stock_identity(db, trade_date: str | None = None, *, sample: int = 8) -> dict[str, Any]:
    """stocks / stock_metadata_history / daily_kbars 活跃代码三方对账。

    活跃代码 = 最近交易日 daily_kbars 中 volume>0 的代码。issues 非空即应告警：
    - 活跃代码不在 stocks（新股没进身份表）；
    - 活跃代码在 trade_date 没有可见的元数据区间（回测 / st_hist 会把它当作未知股票）；
    - stocks.is_st 与 trade_date 当天可见区间 is_st 不一致（任一方过期）；
    - 名称含 ST 但 is_st=0（stocks 或区间）；
    - 区间 effective_from 非 ISO 格式、区间重叠。
    """
    td = trade_date or db.execute(text("SELECT MAX(trade_date) FROM daily_kbars")).scalar()
    td = iso_date(td) or str(td)
    q = lambda sql, **p: db.execute(text(sql), p).all()  # noqa: E731
    active = int(q("SELECT COUNT(DISTINCT code) FROM daily_kbars WHERE trade_date=:d AND volume>0", d=td)[0][0] or 0)
    not_in_stocks = q(
        "SELECT DISTINCT k.code FROM daily_kbars k LEFT JOIN stocks s ON s.code=k.code "
        "WHERE k.trade_date=:d AND k.volume>0 AND s.code IS NULL ORDER BY k.code", d=td)
    no_meta = q(
        "SELECT DISTINCT k.code FROM daily_kbars k WHERE k.trade_date=:d AND k.volume>0 AND NOT EXISTS ("
        "SELECT 1 FROM stock_metadata_history h WHERE h.code=k.code AND h.effective_from<=:d "
        "AND (h.effective_to IS NULL OR h.effective_to>=:d)) ORDER BY k.code", d=td)
    stocks_without_meta = q(
        "SELECT s.code FROM stocks s WHERE NOT EXISTS (SELECT 1 FROM stock_metadata_history h WHERE h.code=s.code) "
        "ORDER BY s.code")
    st_mismatch = q(
        "SELECT s.code, s.name, s.is_st, h.is_st FROM stocks s JOIN stock_metadata_history h ON h.code=s.code "
        "AND h.effective_from<=:d AND (h.effective_to IS NULL OR h.effective_to>=:d) "
        "WHERE s.is_st<>h.is_st ORDER BY s.code", d=td)
    st_name_not_flagged = q(
        "SELECT s.code, s.name FROM stocks s WHERE UPPER(s.name) LIKE '%ST%' AND s.is_st=0 ORDER BY s.code")
    meta_name_st_not_flagged = q(
        "SELECT h.code, h.name FROM stock_metadata_history h WHERE h.effective_to IS NULL "
        "AND UPPER(h.name) LIKE '%ST%' AND h.is_st=0 ORDER BY h.code")
    bad_format = int(q("SELECT COUNT(*) FROM stock_metadata_history WHERE effective_from NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'")[0][0] or 0)
    stocks_last_update = q("SELECT MAX(updated_at) FROM stocks")[0][0]

    def ids(rows: list) -> list[str]:
        return [str(r[0]) for r in rows]

    issues: list[str] = []
    if not_in_stocks:
        issues.append(f"{td} 活跃代码不在 stocks: {len(not_in_stocks)} 只（{','.join(ids(not_in_stocks)[:sample])}…）")
    if no_meta:
        issues.append(f"{td} 活跃代码无可见元数据区间: {len(no_meta)} 只（{','.join(ids(no_meta)[:sample])}…）")
    if stocks_without_meta:
        issues.append(f"stocks 中无任何元数据区间: {len(stocks_without_meta)} 只（{','.join(ids(stocks_without_meta)[:sample])}…）")
    if st_mismatch:
        issues.append(f"stocks.is_st 与 {td} 元数据 is_st 不一致: {len(st_mismatch)} 只（{','.join(ids(st_mismatch)[:sample])}…）")
    if st_name_not_flagged:
        issues.append(f"stocks 名称含 ST 但 is_st=0: {len(st_name_not_flagged)} 只（{','.join(ids(st_name_not_flagged)[:sample])}…）")
    if meta_name_st_not_flagged:
        issues.append(f"当前元数据名称含 ST 但 is_st=0: {len(meta_name_st_not_flagged)} 行（{','.join(ids(meta_name_st_not_flagged)[:sample])}…）")
    if bad_format:
        issues.append(f"stock_metadata_history.effective_from 非 ISO 格式: {bad_format} 行")
    return {
        "table": "stock_identity:reconcile", "latest": td, "issues": issues,
        "active_codes": active, "not_in_stocks": len(not_in_stocks), "no_visible_metadata": len(no_meta),
        "stocks_without_metadata": len(stocks_without_meta), "st_flag_mismatch": len(st_mismatch),
        "stocks_name_st_not_flagged": len(st_name_not_flagged), "meta_name_st_not_flagged": len(meta_name_st_not_flagged),
        "bad_effective_from_format": bad_format,
        "stocks_last_updated_at": str(stocks_last_update) if stocks_last_update else None,
    }
