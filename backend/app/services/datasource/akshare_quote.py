"""AKShare 全市场实时快照：``quote.realtime`` 的第二备份（promax rt_k 耗尽后的兜底之一）。

链路位置（quote_realtime.py）：promax rt_k → 新浪 realtime_quote → 腾讯 qt（.env 开关）→ **AKShare 快照** → quantdata rt_min → 小石。
只有前面几级没拿到的代码才会来这里（fill 模式），所以平时基本不被调用。

数据源（``ASHARE_QUOTE_AKSHARE_FUNC``）：
* ``sina``（默认，2026-10-26 实测可用）：``ak.stock_zh_a_spot``，新浪 vip.stock.finance.sina.com.cn 分页 70 页约 11s，5576 行，
  带逐行时间戳。收盘后与 tushare daily 对账：close/open/high/low/pre_close/pct_chg 5569 只全部一致；
  volume 单位=股（= daily vol×100），amount 单位=元（= daily amount×1000），不复权。
* ``em``（可选）：``ak.stock_zh_a_spot_em``，东财 push2 分页 56 次。**2026-10-26 实测一次全量翻页后本机 IP 被东财 push2 断连
  （RemoteDisconnected），暂无法对账，默认不用**；字段映射写好但标注“未对账”，成交量按“手”×100。

使用约束（防封 IP / 防污染实盘）：
* 全市场快照缓存 ``CACHE_TTL``（8s）；两次真实拉取至少间隔 ``MIN_INTERVAL``（20s，即每分钟最多 3 次）；失败后指数冷却 60s→10min。
* 拉取在后台线程里单飞（同一时刻只有一个翻页在跑），调用方最多等 ``WAIT_SECONDS``（3.5s）；等不到就抛 DATA_PENDING
  （不计入熔断，由下一级顶上），翻页完成后的结果供下一次调用使用。
* 新鲜度：快照超过 ``MAX_AGE``（45s）不用；盘中要求行时间戳在拉取完成前 90s 内（否则整行丢弃），且全市场至少 60% 的活跃行是新的，
  否则整份快照判为“冻结”丢弃；盘前/午休/收盘后要求时间戳不早于该时段起点（11:30/15:00），并按交易日历标注 trade_date。
* 停牌/无成交（price<=0、pre_close<=0、成交量=0 且开盘=0）、价格不在 [low, high] 内的行直接丢弃，不进入选股路径。
* 来源标签固定 ``source = akshare_sina_snapshot``（或 ``akshare_em_snapshot``），行里带 ``snapshot_age_s``。

开关：``ASHARE_QUOTE_AKSHARE_ENABLED=false`` 整体关闭；管理页 data_source_config(akshare, quote) 可运行时关闭。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutTimeout
from datetime import date, datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from app.services.datasource.gateway import DataSourceError, ErrorKind

logger = logging.getLogger(__name__)

DATASET = "quote.realtime"
TZ = ZoneInfo("Asia/Shanghai")
CACHE_TTL = 8.0          # 缓存几秒：同一份快照在 TTL 内直接复用
MIN_INTERVAL = 20.0      # 两次真实翻页的最短间隔（≤3 次/分钟）
WAIT_SECONDS = 3.5       # 调用方最多等多久
MAX_AGE = 45.0           # 快照最老可用年龄
LIVE_ROW_MAX_LAG = 90.0  # 盘中：行时间戳最多落后拉取完成时刻多少秒
LIVE_FRESH_FRACTION = 0.6
FAIL_BACKOFF = (60.0, 120.0, 300.0, 600.0)
MIN_ROWS = 3000          # 全市场快照行数下限，少于视为残缺

_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ak-snap")
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "snap": None,        # {"rows": {code6: row}, "built_at": monotonic, "wall": datetime, "func": str, "n_raw": int}
    "inflight": None,    # Future
    "last_start": 0.0,   # monotonic
    "fail_streak": 0,
    "cool_until": 0.0,
    "last_error": "",
    "last_ok_wall": None,
}


# ── settings ────────────────────────────────────────────────────────────
def _setting(name: str, default: Any) -> Any:
    try:
        from app.core.config import get_settings

        return getattr(get_settings(), name, default)
    except Exception:  # noqa: BLE001
        return default


def enabled() -> bool:
    return bool(_setting("quote_akshare_enabled", True))


def func_name() -> str:
    v = str(_setting("quote_akshare_func", "sina") or "sina").strip().lower()
    return v if v in ("sina", "em") else "sina"


# ── time helpers ────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(TZ)


def _is_trading_day(day: date) -> bool:
    try:
        from app.services.trade_calendar import is_open

        return bool(is_open(day))
    except Exception:  # noqa: BLE001 - 日历不可用：退回工作日规则（与 rt_k 路径一致）
        return day.weekday() < 5


def _prev_trading_day(day: date) -> date:
    from datetime import timedelta

    d = day - timedelta(days=1)
    for _ in range(15):
        if _is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


def phase(now: datetime, *, trading_day: bool | None = None) -> str:
    """pre | morning | lunch | afternoon | after | closed（非交易日）。"""
    td = _is_trading_day(now.date()) if trading_day is None else trading_day
    if not td:
        return "closed"
    hm = (now.hour, now.minute)
    if hm < (9, 25):
        return "pre"
    if hm < (9, 30):
        return "pre"  # 集合竞价撮合：价格不连续，当作盘前
    if hm < (11, 30) or (hm == (11, 30) and now.second == 0):
        return "morning"
    if hm < (13, 0):
        return "lunch"
    if hm < (15, 0) or (hm == (15, 0) and now.second < 5):
        return "afternoon"
    return "after"


def _hms(v: Any) -> int | None:
    try:
        h, m, s = str(v).strip().split(":")[:3]
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:  # noqa: BLE001
        return None


def _f(v: Any) -> float:
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


# ── normalisation ───────────────────────────────────────────────────────
def _ts_code(code6: str, prefix: str) -> str:
    return f"{code6}.{ {'sh': 'SH', 'sz': 'SZ', 'bj': 'BJ'}.get(prefix, 'SZ') }"


def _raw_sina(df) -> list[dict[str, Any]]:
    """ak.stock_zh_a_spot（新浪）→ 统一的中间行。volume 股、amount 元、不复权。"""
    out = []
    for r in df.to_dict("records"):
        sym = str(r.get("代码") or "").lower()
        if len(sym) < 8:
            continue
        out.append({"prefix": sym[:2], "code": sym[2:], "name": str(r.get("名称") or "").strip(),
                    "price": _f(r.get("最新价")), "open": _f(r.get("今开")), "pre_close": _f(r.get("昨收")),
                    "high": _f(r.get("最高")), "low": _f(r.get("最低")),
                    "volume": _f(r.get("成交量")), "amount": _f(r.get("成交额")), "time": str(r.get("时间戳") or "")})
    return out


def _raw_em(df) -> list[dict[str, Any]]:
    """ak.stock_zh_a_spot_em（东财）→ 统一的中间行。成交量“手”×100=股；无逐行时间戳（以拉取完成时刻为准）。**未与 promax/日线对账**。"""
    out = []
    for r in df.to_dict("records"):
        code = str(r.get("代码") or "").strip()
        if len(code) != 6:
            continue
        prefix = "sh" if code.startswith(("6", "9")) and not code.startswith("92") else ("bj" if code.startswith(("92", "4", "8")) else "sz")
        out.append({"prefix": prefix, "code": code, "name": str(r.get("名称") or "").strip(),
                    "price": _f(r.get("最新价")), "open": _f(r.get("今开")), "pre_close": _f(r.get("昨收")),
                    "high": _f(r.get("最高")), "low": _f(r.get("最低")),
                    "volume": _f(r.get("成交量")) * 100.0, "amount": _f(r.get("成交额")), "time": ""})
    return out


def build_snapshot(raw: list[dict[str, Any]], *, now: datetime, func: str, trading_day: bool | None = None) -> dict[str, Any]:
    """原始行 → 经过新鲜度/合法性筛选的 {code6: row}。纯函数（便于测试）。

    抛 DataSourceError(BAD_DATA)：行数太少、盘中全市场冻结。"""
    if len(raw) < MIN_ROWS:
        raise DataSourceError(ErrorKind.BAD_DATA, f"snapshot only {len(raw)} rows (< {MIN_ROWS})", provider="akshare_snapshot", dataset=DATASET)
    td = _is_trading_day(now.date()) if trading_day is None else trading_day
    ph = phase(now, trading_day=td)
    if ph in ("closed", "pre"):
        # 非交易日 / 盘前：新浪/东财给的是上一交易日收盘快照
        quote_day = _prev_trading_day(now.date())
    else:
        quote_day = now.date()
    tag = "akshare_sina_snapshot" if func == "sina" else "akshare_em_snapshot"
    now_s = now.hour * 3600 + now.minute * 60 + now.second
    floor_s = {"morning": None, "afternoon": None, "lunch": 11 * 3600 + 30 * 60, "after": 15 * 3600, "pre": 15 * 3600, "closed": 15 * 3600}[ph]
    live = ph in ("morning", "afternoon")

    rows: dict[str, dict[str, Any]] = {}
    active = fresh = 0
    dropped = {"suspended": 0, "bad_price": 0, "stale": 0}
    for r in raw:
        price, pre, hi, lo, op, vol = r["price"], r["pre_close"], r["high"], r["low"], r["open"], r["volume"]
        if price <= 0 or pre <= 0 or (vol <= 0 and op <= 0):
            dropped["suspended"] += 1
            continue
        if lo > 0 and hi > 0 and not (lo * 0.995 <= price <= hi * 1.005 and hi >= lo):
            dropped["bad_price"] += 1
            continue
        t = _hms(r["time"]) if r["time"] else None
        if func == "sina":
            if t is None:
                dropped["stale"] += 1
                continue
            if live:
                active += 1
                if not (now_s - LIVE_ROW_MAX_LAG <= t <= now_s + 30):
                    dropped["stale"] += 1
                    continue
                fresh += 1
            elif floor_s is not None and t < floor_s:
                dropped["stale"] += 1
                continue
            qt = r["time"]
        else:  # em：无逐行时间戳，只能靠整体新鲜度（拉取完成时刻）
            if live:
                active += 1
                fresh += 1
            qt = now.strftime("%H:%M:%S")
        code6 = r["code"]
        rows[code6] = {
            "code": code6, "ts_code": _ts_code(code6, r["prefix"]), "name": r["name"],
            "trade_date": quote_day.isoformat(), "quote_time": qt, "price": price, "open": op, "pre_close": pre,
            "high": hi, "low": lo, "volume": vol, "amount": r["amount"], "source": tag,
        }
    if live and func == "sina" and active and fresh / active < LIVE_FRESH_FRACTION:
        raise DataSourceError(ErrorKind.BAD_DATA, f"snapshot frozen: only {fresh}/{active} rows within {LIVE_ROW_MAX_LAG:.0f}s",
                              provider="akshare_snapshot", dataset=DATASET)
    if not rows:
        raise DataSourceError(ErrorKind.BAD_DATA, f"no usable rows (dropped {dropped})", provider="akshare_snapshot", dataset=DATASET)
    return {"rows": rows, "dropped": dropped, "phase": ph, "n_raw": len(raw)}


# ── fetch plumbing ──────────────────────────────────────────────────────
def _pull_frame(func: str):
    try:
        from app.core import metrics as _m

        if _m._process_role() == "pytest":  # 测试进程里绝不真去翻第三方页面（需要的测试自己注入 puller）
            raise DataSourceError(ErrorKind.DISABLED, "akshare snapshot disabled under pytest", provider="akshare_snapshot", dataset=DATASET)
    except DataSourceError:
        raise
    except Exception:  # noqa: BLE001
        pass
    import akshare as ak

    return ak.stock_zh_a_spot() if func == "sina" else ak.stock_zh_a_spot_em()


def _metric(outcome: str, **kw: Any) -> None:
    try:
        from app.core import metrics as _m

        _m.inc("akshare_snapshot", outcome=outcome, **kw)
    except Exception:  # noqa: BLE001
        pass


def _refresh(func: str, puller: Callable[[str], Any]) -> None:
    """后台线程：翻页拉取 → 校验 → 写缓存。异常记入冷却，不外抛（调用方从 Future/状态看结果）。"""
    t0 = time.monotonic()
    try:
        df = puller(func)
        now = _now()
        raw = (_raw_sina if func == "sina" else _raw_em)(df)
        snap = build_snapshot(raw, now=now, func=func)
        snap.update(built_at=time.monotonic(), wall=now, func=func, seconds=round(time.monotonic() - t0, 1))
        with _LOCK:
            _STATE.update(snap=snap, fail_streak=0, cool_until=0.0, last_error="", last_ok_wall=now)
        _metric("refresh_ok", func=func)
        logger.info("akshare %s snapshot ok: %d usable / %d raw rows in %.1fs dropped=%s", func, len(snap["rows"]), snap["n_raw"],
                    snap["seconds"], snap["dropped"])
    except BaseException as exc:  # noqa: BLE001
        with _LOCK:
            streak = int(_STATE["fail_streak"]) + 1
            back = FAIL_BACKOFF[min(streak - 1, len(FAIL_BACKOFF) - 1)]
            _STATE.update(fail_streak=streak, cool_until=time.monotonic() + back, last_error=f"{type(exc).__name__}: {str(exc)[:160]}")
        _metric("refresh_fail", func=func)
        logger.warning("akshare %s snapshot failed (#%d, cooling %.0fs): %s", func, streak, back, exc)
        raise


def status() -> dict[str, Any]:
    """监控用：最近一次成功时间、年龄、冷却、连续失败。"""
    with _LOCK:
        s = _STATE["snap"]
        return {"enabled": enabled(), "func": func_name(), "has_snapshot": bool(s),
                "age_s": round(time.monotonic() - s["built_at"], 1) if s else None,
                "rows": len(s["rows"]) if s else 0, "fail_streak": _STATE["fail_streak"],
                "cooling_s": max(0.0, round(_STATE["cool_until"] - time.monotonic(), 1)), "last_error": _STATE["last_error"],
                "last_ok": _STATE["last_ok_wall"].isoformat(timespec="seconds") if _STATE["last_ok_wall"] else None}


def reset_for_tests() -> None:
    with _LOCK:
        _STATE.update(snap=None, inflight=None, last_start=0.0, fail_streak=0, cool_until=0.0, last_error="", last_ok_wall=None)


class AkshareSnapshotProvider:
    name = "akshare_snapshot"
    config_keys = (("akshare", "quote"),)

    def __init__(self, puller: Callable[[str], Any] | None = None) -> None:
        self._puller = puller or _pull_frame

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET and enabled()

    def _usable(self) -> dict[str, Any] | None:
        s = _STATE["snap"]
        return s if s is not None and time.monotonic() - s["built_at"] <= MAX_AGE else None

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        keys = [str(k) for k in query["keys"]]
        func = func_name()
        fut: Future | None = None
        with _LOCK:
            s = self._usable()
            fresh = s is not None and time.monotonic() - s["built_at"] <= CACHE_TTL and s["func"] == func
            if not fresh:
                inflight = _STATE["inflight"]
                if inflight is not None and not inflight.done():
                    fut = inflight  # 已有翻页在跑：等它
                else:
                    now_m = time.monotonic()
                    if now_m < _STATE["cool_until"]:
                        if s is None:
                            _metric("cooling")
                            raise DataSourceError(ErrorKind.DATA_PENDING, f"akshare snapshot cooling {_STATE['cool_until'] - now_m:.0f}s "
                                                  f"({_STATE['last_error']})", provider=self.name, dataset=dataset)
                    elif now_m - _STATE["last_start"] < MIN_INTERVAL:
                        if s is None:
                            _metric("rate_limited")
                            raise DataSourceError(ErrorKind.DATA_PENDING, "akshare snapshot rate-limited (min interval)", provider=self.name, dataset=dataset)
                    else:
                        _STATE["last_start"] = now_m
                        fut = _POOL.submit(_refresh, func, self._puller)
                        _STATE["inflight"] = fut
        if fut is not None:
            try:
                fut.result(timeout=WAIT_SECONDS)
            except _FutTimeout:
                pass  # 翻页还在跑：看缓存里有没有仍在 MAX_AGE 内的旧快照，没有就让下一级顶上
            except Exception as exc:  # noqa: BLE001
                with _LOCK:
                    s2 = self._usable()
                if s2 is None:
                    if isinstance(exc, DataSourceError):
                        raise
                    raise DataSourceError(ErrorKind.UPSTREAM, f"akshare snapshot failed: {exc}", provider=self.name, dataset=dataset) from exc
        with _LOCK:
            s = self._usable()
        if s is None:
            _metric("warming")
            raise DataSourceError(ErrorKind.DATA_PENDING, "akshare snapshot warming (full-market paging in progress)", provider=self.name, dataset=dataset)
        age = round(time.monotonic() - s["built_at"], 1)
        out: dict[str, dict[str, Any]] = {}
        for k in keys:
            row = s["rows"].get(k)
            if row is not None:
                out[k] = {**row, "snapshot_age_s": age}
        _metric("served", rows=str(len(out)))
        return out
