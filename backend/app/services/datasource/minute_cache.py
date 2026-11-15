"""按需分钟线缓存：只拉"回测真正用到的股票与区间"，不囤全市场。

背景（2026-11-15，架构优化项③）
-------------------------------
全市场 3 年 1 分钟约 10 亿行、20GB，本地不可能全量维护；而原先唯一的历史分钟源
小石（``data/xiaoshi_min_all``）已于 2026-11-15 永久退役并删除存档，实时补数只剩
``baostock``（5min+）与 ``tushare stk_mins``（1min，单次 8000 行）。上游一旦返回满
8000 行，``tushare_client._fetch_tushare_minute_kbars`` 会**整窗放弃返回空列表**，
于是长区间回测的分钟线呈"腰斩"状。

本模块把这件事从"囤数据"改成"按需取用"：

1. **按月落盘**：``backend/data/minute_cache/{freq}/{code}/{YYYY-MM}.parquet``，
   命中直接读盘，不再打上游（研究脚本反复回跑同一区间是常态）。
2. **只补缺口**：给定区间，先看哪些月已有文件，只对缺的月发请求。
3. **空结果二分重拉**：某窗口返回空时自动对半切分重试，直到 ``_BISECT_MIN_DAYS``
   天为止，绕开"满 8000 行整单放弃"和偶发限流导致的空洞。

回测/研究侧的用法::

    from app.services.datasource.minute_cache import ensure

    data = ensure("600000", "2024-01-01", "2024-12-31")   # 首次拉取，之后走缓存
    rows = data["rows"]                                    # list[dict]，按时间升序

批量预热（回测前一次性做准备，可放 Prefect）::

    python -m app.services.datasource.minute_cache --codes 600000,000001 --start 2024-01-01 --end 2024-12-31

缓存不参与调度：由研究流程显式调用，避免把 20GB 的写入压到盘后主流程上。
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

DEFAULT_FREQ = "1min"
_BISECT_MIN_DAYS = 5  # 二分到 5 天窗口就停（再细就是交易日缺失，不是截断）
_BISECT_MAX_CALLS = 12  # 单个月窗口最多额外重试次数，防止故障时打爆上游

_TIME_KEYS = ("trade_time", "trade_date", "datetime", "time", "date")


def cache_root() -> Path:
    """缓存根目录：``minute_cache_dir`` 优先，默认 ``backend/data/minute_cache``。"""
    try:
        from app.core.config import get_settings

        configured = str(getattr(get_settings(), "minute_cache_dir", "") or "").strip()
    except Exception:  # noqa: BLE001
        configured = ""
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / "data" / "minute_cache"


def _parse(value: str) -> date:
    raw = str(value or "").strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {value!r}（需 YYYY-MM-DD 或 YYYYMMDD）")


def month_windows(start_date: str, end_date: str) -> list[tuple[str, str, str]]:
    """把区间切到自然月：``[(YYYY-MM, start, end), ...]``，首尾月按传入区间裁剪。"""
    start, end = _parse(start_date), _parse(end_date)
    if start > end:
        return []
    out: list[tuple[str, str, str]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = cursor.replace(day=last_day)
        window_start = max(start, cursor)
        window_end = min(end, month_end)
        out.append((f"{cursor.year:04d}-{cursor.month:02d}", window_start.isoformat(), window_end.isoformat()))
        cursor = month_end + timedelta(days=1)
    return out


def _cache_path(code: str, freq: str, month: str) -> Path:
    return cache_root() / freq / str(code).strip() / f"{month}.parquet"


def _read_month(code: str, freq: str, month: str) -> list[dict[str, Any]] | None:
    path = _cache_path(code, freq, month)
    if not path.exists():
        return None
    try:
        import pandas as pd

        frame = pd.read_parquet(path)
        # parquet 往返会把 None 变成 NaN、把整数列变成 float——下游按 dict 取数会拿到 nan
        # 参与计算。这里统一还原成 None，保证"读缓存"与"读上游"拿到的行语义一致。
        frame = frame.where(pd.notna(frame), None)
        return [{k: v for k, v in rec.items()} for rec in frame.to_dict("records")]
    except Exception as exc:  # noqa: BLE001 - 缓存损坏视为未命中，重拉即可
        logger.warning("minute_cache 读取失败，按未命中处理 path=%s err=%s", path, exc)
        return None


def _write_month(code: str, freq: str, month: str, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    import pandas as pd

    path = _cache_path(code, freq, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pd.DataFrame(rows).to_parquet(tmp, index=False)
    tmp.replace(path)  # 原子替换：并发读不会读到半个文件


def _time_key(row: dict[str, Any]) -> str:
    for key in _TIME_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def enabled() -> bool:
    """缓存总开关（``minute_cache_enabled``，默认开）。"""
    try:
        from app.core.config import get_settings

        return bool(getattr(get_settings(), "minute_cache_enabled", True))
    except Exception:  # noqa: BLE001
        return True


def _fetch_window(
    code: str, start_date: str, end_date: str, freq: str, trace: Any = None
) -> tuple[list[dict[str, Any]], Any]:
    """单窗口取数（走网关 ``bar.minute`` 链：baostock / stk_mins / web 备份）。

    返回 ``(rows, gateway_result)``，后者用于在"确实没数据"时透传上游 attempts。
    ``use_cache=False`` 是必须的——否则会经过 :func:`bar_minute.fetch` 又回到这里，形成回环。
    """
    from app.services.datasource import bar_minute

    res = bar_minute.fetch(code, start_date, end_date, freq, trace, use_cache=False)
    rows = [dict(r) for r in (res.rows or [])]
    return rows, res


def _fetch_with_bisect(
    code: str, start_date: str, end_date: str, freq: str, trace: Any = None, allow_bisect: bool = True
) -> tuple[list[dict[str, Any]], int, Any]:
    """取一个窗口；为空则对半切分重试。返回 ``(rows, extra_calls, last_gateway_result)``。

    ``allow_bisect=False``（当月）不做二分：当月本来就不落盘，救回来也没地方存，而盘中每次
    轮询都多打十几次上游只会加速额度耗尽。
    """
    calls = 0
    rows, res = _fetch_window(code, start_date, end_date, freq, trace)
    if rows or not allow_bisect:
        return rows, calls, res

    start, end = _parse(start_date), _parse(end_date)
    span = (end - start).days + 1
    if span <= _BISECT_MIN_DAYS:
        return [], calls, res

    stack: list[tuple[date, date]] = []
    mid = start + timedelta(days=span // 2 - 1)
    stack.append((start, mid))
    stack.append((mid + timedelta(days=1), end))
    collected: list[dict[str, Any]] = []
    while stack and calls < _BISECT_MAX_CALLS:
        s, e = stack.pop(0)
        calls += 1
        part, res = _fetch_window(code, s.isoformat(), e.isoformat(), freq, trace)
        if part:
            collected.extend(part)
            continue
        length = (e - s).days + 1
        if length <= _BISECT_MIN_DAYS:
            continue  # 太窄了，空就是真没数据（周末/停牌）
        cut = s + timedelta(days=length // 2 - 1)
        stack.append((s, cut))
        stack.append((cut + timedelta(days=1), e))
    return collected, calls, res


def _persistable(month: str) -> bool:
    """只有已经走完的月份才落盘。

    当月（以及未来月）的数据还会继续增长，写进 parquet 就再也不会刷新——回测拿到的会是
    "当月只到某一天为止"的陈旧切片。这类请求每次都走上游，正确性优先。
    """
    return month < datetime.now().strftime("%Y-%m")


def ensure(
    code: str,
    start_date: str,
    end_date: str,
    freq: str = DEFAULT_FREQ,
    *,
    refresh: bool = False,
    trace: Any = None,
) -> dict[str, Any]:
    """确保本地有 ``code`` 在 ``[start_date, end_date]`` 的分钟线，返回合并后的行。

    * ``refresh=True``：忽略已有缓存，强制重拉（修复过期/错误缓存时用）。
    * 返回 ``{"code","freq","start","end","rows","months_cached","months_fetched","months_volatile",
      "empty_months","extra_calls","upstream"}``；``months_volatile`` 是"取到了但按规则不落盘"的月份。
    """
    body = str(code or "").strip()
    if not body:
        raise ValueError("code 不能为空")
    windows = month_windows(start_date, end_date)
    rows: list[dict[str, Any]] = []
    cached: list[str] = []
    fetched: list[str] = []
    volatile: list[str] = []
    empty: list[str] = []
    extra_calls = 0

    last_upstream = None
    for month, win_start, win_end in windows:
        hit = None if refresh else _read_month(body, freq, month)
        if hit is not None:
            cached.append(month)
            rows.extend(hit)
            continue
        part, calls, last_upstream = _fetch_with_bisect(
            body, win_start, win_end, freq, trace, allow_bisect=_persistable(month)
        )
        extra_calls += calls
        if part:
            if _persistable(month):
                _write_month(body, freq, month, part)
                fetched.append(month)
            else:
                volatile.append(month)   # 当月不落盘，每次重拉保证完整
            rows.extend(part)
        else:
            empty.append(month)
            logger.info("minute_cache 空窗口 code=%s month=%s freq=%s", body, month, freq)

    rows.sort(key=_time_key)
    return {
        "code": body,
        "freq": freq,
        "start": start_date,
        "end": end_date,
        "rows": rows,
        "months_cached": cached,
        "months_fetched": fetched,
        "months_volatile": volatile,
        "empty_months": empty,
        "extra_calls": extra_calls,
        "upstream": last_upstream,
    }


def serve(
    code: str, start_date: str, end_date: str, freq: str = DEFAULT_FREQ, trace: Any = None
) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
    """``bar.minute.fetch`` 用的入口：``(rows, meta, upstream_gateway_result)``。

    缓存关掉或代码非法时不抛异常，返回空行 + ``upstream=None``，由调用方回落纯网关。
    """
    if not enabled():
        return [], {"disabled": True}, None
    try:
        meta = ensure(code, start_date, end_date, freq, trace=trace)
    except Exception as exc:  # noqa: BLE001 - 缓存故障绝不能拖垮取数主链路
        logger.warning("minute_cache serve 失败 code=%s err=%s", code, exc)
        return [], {"error": str(exc)}, None
    return meta["rows"], meta, meta.get("upstream")


def warm(
    codes: Iterable[str], start_date: str, end_date: str, freq: str = DEFAULT_FREQ, *, refresh: bool = False
) -> dict[str, dict[str, Any]]:
    """批量预热（回测前调用）。单个代码失败不影响其余。"""
    out: dict[str, dict[str, Any]] = {}
    for code in codes:
        body = str(code or "").strip()
        if not body:
            continue
        try:
            out[body] = ensure(body, start_date, end_date, freq, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - 批量任务不因单只中断
            logger.warning("minute_cache warm 失败 code=%s err=%s", body, exc)
            out[body] = {"code": body, "error": str(exc), "rows": []}
    return out


def stats(code: str, freq: str = DEFAULT_FREQ) -> dict[str, Any]:
    """查看某只票的本地缓存覆盖情况（排查"为什么回测没数据"用）。"""
    folder = cache_root() / freq / str(code).strip()
    if not folder.is_dir():
        return {"code": str(code), "freq": freq, "months": [], "bytes": 0}
    files = sorted(folder.glob("*.parquet"))
    return {
        "code": str(code),
        "freq": freq,
        "months": [f.stem for f in files],
        "bytes": sum(f.stat().st_size for f in files),
    }


def _main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="按需预热分钟线缓存（不囤全市场）")
    parser.add_argument("--codes", required=True, help="逗号分隔的 6 位代码")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--freq", default=DEFAULT_FREQ, help="1min / 5min / 15min / 30min / 60min")
    parser.add_argument("--refresh", action="store_true", help="忽略已有缓存强制重拉")
    parser.add_argument("--stats-only", action="store_true", help="只看本地覆盖情况，不联网")
    args = parser.parse_args(argv)

    if args.stats_only:
        print(json.dumps(
            {c.strip(): stats(c.strip(), args.freq) for c in args.codes.split(",") if c.strip()},
            ensure_ascii=False, indent=2,
        ))
        return 0

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    result = warm(codes, args.start, args.end, args.freq, refresh=args.refresh)
    summary = {
        code: {
            "rows": len(item.get("rows") or []),
            "months_cached": len(item.get("months_cached") or []),
            "months_fetched": len(item.get("months_fetched") or []),
            "empty_months": item.get("empty_months") or [],
            "extra_calls": item.get("extra_calls", 0),
            "error": item.get("error"),
        }
        for code, item in result.items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(not v["error"] for v in summary.values()) else 1


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(_main())
