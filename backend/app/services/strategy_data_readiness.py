"""出票前按策略检查依赖数据（数据就绪闸门）。

需求（2026-11-05 用户）：每个策略出票前检查自己依赖的数据；依赖的数据不是最新，该策略当天不出票，
记成「数据未就绪」，并在前端标出缺了哪份数据、最新到哪天、应该到哪天。其它策略照常出。

设计：
- 不另造一套巡检。表名、日期列、中文名、目录阈值全部取自 data_quality_monitor（DB_DATASETS / DIR_DATASETS），
  交易日滞后用同一个 trade_lag。这里只多做一件事：按出票日 as_of「当时」的口径判（MAX(日期) WHERE 日期 <= as_of），
  并给每个策略声明依赖清单（STRATEGY_DEPS）。
- 只拦实盘：短线单策略 formal run（scheduler._run_standalone_recommendations）和中长线 generate_picks(data_gate=True)。
  历史回放 / 滚动重算不开闸门，回测与 R12 口径不变；也不改任何策略的选股规则。
- 闸门本身出错时放行并告警（fail-open）：短线已有全局健康闸门（日K/报价/指数/涨停板）兜底，
  不能因为检查代码的 bug 让全部策略停出。
- 结果写 output/strategy_readiness/<日期>.json（每个策略一条，补跑时覆盖），盘后出票检查、健康分和前端都读这里。

依赖的判法：
- 日频表（日K、资金流、概念板块、涨停板、龙虎榜…）：最新日期必须到 as_of（允许滞后写在每条依赖上，默认 0）。
  四张逐股全市场表（日K、每日基本面、资金流、收盘报价）再看当天条数：不到前 5 个交易日中位数的一半算「没写全」。
- 事件 / 季度型本地历史（逐股财报、业绩预告、分红目录）：只在超过 data_quality_monitor 里的红线天数没更新时才拦
  （财报/预告 120 天、分红 150 天），因为它们只用于「是不是第一次公告」「过去 8 期 SUE」这类历史判断，本来就按季更新。
- 公告类事件（回购、预告、激励、大宗、财报指标本身）是出票时经数据网关现拉的，拉不到候选函数已经返回 no_data，
  不在这里重复判；data/midterm/events/*_live 这些 parquet 是策略自己出票时追加的，用它拦会把自己永久锁死，故不列入。
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import statistics
import time
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import bindparam, text

from app.services import data_quality_monitor as dq

logger = logging.getLogger(__name__)

STORE_DIR = Path(__file__).resolve().parents[2] / "output" / "strategy_readiness"
STATUS = "data_not_ready"  # 与 no_data / data_error 并列的单策略日状态

# ---------------------------------------------------------------- 数据集
# 中文名给投资外行看，尽量短；表/目录与阈值来自 data_quality_monitor。
PLAIN_LABELS: dict[str, str] = {
    "daily_kbars": "日K行情",
    "daily_basic": "每日市值/估值",
    "capital_flow": "资金流向",
    "stock_quotes": "收盘行情快照",
    "concept_sector": "概念板块",
    "concept_sector_stock": "概念板块成分股",
    "limit_up_board": "涨停板名单",
    "dragon_tiger": "龙虎榜",
    "fina_hist": "历年财报（本地）",
    "forecast_hist": "历史业绩预告（本地）",
    "dividend_hist": "历史分红（本地）",
}
_DB_KEYS = {"daily_kbars": "db:daily_kbars", "daily_basic": "db:daily_basic", "capital_flow": "db:capital_flow",
            "stock_quotes": "db:stock_quotes", "concept_sector": "db:concept_sector",
            "concept_sector_stock": "db:concept_sector_stock", "limit_up_board": "db:limit_up_board",
            "dragon_tiger": "db:dragon_tiger"}
_DIR_KEYS = {"fina_hist": "file:midterm/fina", "forecast_hist": "file:midterm/forecast",
             "dividend_hist": "file:midterm/dividend"}
# 逐股全市场表：当天条数 < 前 5 个交易日中位数 × COVERAGE_MIN 视为没写全（与巡检的「降 >50% 红」同一条线）。
# 概念板块/涨停板/龙虎榜条数天然大幅波动（概念成分曾因上游截断在 83 与 8 万条之间跳），不做条数判断，只看日期。
COVERAGE_TABLES = frozenset({"daily_kbars", "daily_basic", "capital_flow", "stock_quotes"})
COVERAGE_MIN = dq.DROP_RED
COVERAGE_BASELINE_DAYS = 5


def _db_spec(key: str) -> dict[str, Any]:
    mk = _DB_KEYS[key]
    return next(s for s in dq.DB_DATASETS if s["key"] == mk)


def _dir_spec(key: str) -> dict[str, Any]:
    mk = _DIR_KEYS[key]
    return next(s for s in dq.DIR_DATASETS if s["key"] == mk)


# ---------------------------------------------------------------- 依赖清单
# 每条依赖 = (数据集, 允许滞后的交易日数)。目录型数据集的第二项不用（按巡检红线天数判）。
Dep = tuple[str, int]

# 短线四个现役策略（特征来自各自 strategy.yaml 里用到的字段，见 app/services/factors/shortline.py）：
# - 日K行情：涨跌幅、均线、RSI、近 20 日涨幅/回撤、量比等全部技术面字段；
# - 收盘行情快照：成交额、换手率、流通市值（universe 门槛）；
# - 涨停板名单：涨停家数、情绪相位、连板梯队、过热分；
# - 资金流向：主力净流入、资金共振分（缺了会悄悄退化成「涨幅+量比」估算）；
# - 概念板块/成分股：是否主线题材、板块梯队（缺了会退化成行业分组）；
# - 龙虎榜：只有陈小群用（上榜、买方席位分散度）。
# 日K/收盘快照/涨停板同时也在全局健康闸门里（data_health_service._RECOMMENDATION_REQUIRED_TABLES），这里再列一遍是为了按策略标出原因。
_SHORT_BASE: tuple[Dep, ...] = (
    ("daily_kbars", 0), ("stock_quotes", 0), ("limit_up_board", 0),
    ("capital_flow", 0), ("concept_sector", 0), ("concept_sector_stock", 0),
)
SHORT_DEPS: dict[str, tuple[Dep, ...]] = {
    "chao-gu-yang-jia-skill": _SHORT_BASE,
    "chen-xiaoqun-skill": _SHORT_BASE + (("dragon_tiger", 0),),
    "oversold-capital-reversal": _SHORT_BASE,
    "sector-relative-strength-leader": _SHORT_BASE,
    # 市场 RSI6 用日K；成交额/涨跌停家数用收盘快照。不需要涨停池/资金流/概念板块。
    "rsi6-capitulation": (("daily_kbars", 0), ("stock_quotes", 0)),
}
# 新上线、还没写进上表的短线策略：只按全局闸门那三份判，不凭空多拦（测试会提醒把新策略补进 SHORT_DEPS）。
SHORT_DEFAULT: tuple[Dep, ...] = (("daily_kbars", 0), ("stock_quotes", 0), ("limit_up_board", 0))

# 中长线：按 STRATEGIES[sid]["kind"] 声明。
# - 小市值周度这一族按当天市值排名、算小盘指数 20 日线：日K和每日基本面都必须到当天（原来就会标 daily_basic_stale）。
# - 事件型：日K必须到当天（信号日涨跌、放量、20 日均额都用当天）；每日基本面只用于流动性过滤里的市值和大宗交易的流通市值折算
#   （按收盘价换算到当天），晚 1 个交易日影响很小，允许滞后 1，免得 daily_basic 晚到时整批事件策略停出。
# - SUE 一族：「过去 8 期扣非」读本地逐股财报；预告一族：「是不是第一次预告」读本地逐股预告；分红一族：去年同期派现读本地分红。
_SMALLCAP: tuple[Dep, ...] = (("daily_kbars", 0), ("daily_basic", 0))
_EVENT: tuple[Dep, ...] = (("daily_kbars", 0), ("daily_basic", 1))
_SUE = _EVENT + (("fina_hist", 0),)
_FORECAST = _EVENT + (("forecast_hist", 0),)
_DIVIDEND = _EVENT + (("dividend_hist", 0),)
MID_KIND_DEPS: dict[str, tuple[Dep, ...]] = {
    "smallcap": _SMALLCAP,  # mid-small-cap-weekly 没写 kind
    "lowprice_small": _SMALLCAP, "smallcap_div": _SMALLCAP, "smallcap_lowps": _SMALLCAP, "smallcap_rev": _SMALLCAP,
    # 红利低波（月末）：市值用周度快照 total_share×收盘（快照缺时经网关现拉），只硬依赖日K
    "dividend_lowvol": (("daily_kbars", 0),), "lowturn_lowvol": (("daily_kbars", 0),),
    "repurchase": _EVENT, "repurchase_underreact": _EVENT,
    "event_dip": _EVENT,  # §993 回购预案∪期权激励 × 公告前深跌：r20 与信号日涨跌都用当天日K
    "ea_dip": _EVENT,  # §1000 业绩加速 × 公告前深跌（事件池同 earnings_accel）
    "divplan_dip": _DIVIDEND,  # §997 分红预案 × 公告前深跌：「同报告期第一次预案」读本地分红历史
    "earnings_accel": _EVENT, "earnings_accel_vol": _EVENT, "ea_td_vol": _EVENT, "ea_cas_vol": _EVENT,
    "sue_accel": _SUE, "sue_accel_vol": _SUE, "sue_td_vol": _SUE, "sexp_sue_vol": _SUE, "ocfs_sue_vol": _SUE,
    "qn_sue_vol": _SUE, "curr_sue_vol": _SUE,
    "forecast_beat": _FORECAST, "forecast_beat_underreact": _FORECAST, "forecast_turnaround": _FORECAST,
    "forecast_preincrease": _FORECAST, "forecast_preincrease_underreact": _FORECAST,
    "forecast_preincrease_vol": _FORECAST, "forecast_preincrease_underreact_vol": _FORECAST,
    "forecast_slight": _FORECAST, "forecast_slight_underreact": _FORECAST, "forecast_slight_vol": _FORECAST,
    "incentive_option": _EVENT, "incentive_underreact": _EVENT, "incentive_vol": _EVENT,
    "block_trade_premium": _EVENT,
    "sexp_yoy": _EVENT, "sexp_vol": _EVENT, "fina_yoy": _EVENT, "fina_vol": _EVENT,
    "dinc_yoy": _DIVIDEND, "dinc_vol": _DIVIDEND,
    "td_vol": _EVENT, "cas_vol": _EVENT, "cg_streak": _EVENT, "ncs_switch": _EVENT,
    # §1011 深跌×行业热点×企稳（mid-sector-dip-stabilize）：日K+行业缓存，不读财报/预告
    "sector_dip_stabilize": _EVENT,
}
MID_DEFAULT: tuple[Dep, ...] = _EVENT


def deps_for(strategy_id: str) -> tuple[Dep, ...]:
    if strategy_id in SHORT_DEPS:
        return SHORT_DEPS[strategy_id]
    from app.services import midterm_service as ms

    meta = ms.STRATEGIES.get(strategy_id) or ms.RETIRED_STRATEGIES.get(strategy_id)
    if meta is None:
        return SHORT_DEFAULT
    return MID_KIND_DEPS.get(str(meta.get("kind") or "smallcap"), MID_DEFAULT)


# ---------------------------------------------------------------- 检查
def _mmdd(d: str | None) -> str:
    return d[5:] if d and len(d) >= 10 else (d or "无")


def _open_days_with(db, as_of: str) -> list[str]:
    days = dq.load_open_days(db)
    if as_of not in set(days):
        # 交易日历缺 as_of（日历没续上或传入了日历外的日期）：把 as_of 当交易日补进去，
        # 这样落后的数据至少算滞后 1，不会因为日历不全被误判成「已最新」。
        logger.warning("交易日历里没有 %s（日历最远 %s），数据就绪判断按它是交易日处理", as_of, days[-1] if days else None)
        days = sorted(set(days) | {as_of})
    return days


def _min_ok_day(open_days: list[str], as_of: str, allowed: int) -> str:
    past = [d for d in open_days if d <= as_of]
    return past[-1 - allowed] if len(past) > allowed else (past[0] if past else as_of)


def check_db_dataset(db, key: str, as_of: str, allowed: int, open_days: list[str]) -> dict[str, Any]:
    spec = _db_spec(key)
    table, col = spec["table"], spec["col"]
    latest = dq.norm_date(db.execute(text(f"SELECT MAX(`{col}`) FROM `{table}` WHERE `{col}` <= :d"), {"d": as_of}).scalar())
    lag = dq.trade_lag(open_days, latest, as_of)
    label = PLAIN_LABELS[key]
    out: dict[str, Any] = {"key": key, "label": label, "table": table, "latest": latest, "expected": as_of,
                           "allowed_lag": allowed, "lag_trade_days": lag, "ok": True, "reason": ""}
    if lag is None or lag > allowed:
        out["ok"] = False
        out["reason"] = f"{label} 最新 {_mmdd(latest)}，应为 {_mmdd(as_of)}" + (f"（最多可晚 {allowed} 个交易日）" if allowed else "")
        return out
    if key in COVERAGE_TABLES and latest == as_of:
        past = [d for d in open_days if d <= as_of][-(COVERAGE_BASELINE_DAYS + 1):]
        rows = db.execute(text(f"SELECT `{col}`, COUNT(*) FROM `{table}` WHERE `{col}` IN :ds GROUP BY `{col}`")
                          .bindparams(bindparam("ds", expanding=True)), {"ds": past}).all()
        counts = {dq.norm_date(r[0]): int(r[1]) for r in rows}
        base = [counts[d] for d in past[:-1] if counts.get(d)]
        today = counts.get(as_of, 0)
        out["latest_count"] = today
        if base:
            med = statistics.median(base)
            out["baseline_count"] = int(med)
            if med > 0 and today < COVERAGE_MIN * med:
                out["ok"] = False
                out["reason"] = f"{label} {_mmdd(as_of)} 只写进 {today} 条，平时约 {int(med)} 条（没写全）"
    return out


def check_dir_dataset(key: str, as_of: str, now: _dt.datetime | None = None, base: Path | None = None) -> dict[str, Any]:
    spec = _dir_spec(key)
    item = dq.check_dir_dataset(spec, now=now, base=base)
    label = PLAIN_LABELS[key]
    red_days = int(spec["red_days"])
    oldest_ok = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=red_days)).isoformat()
    latest = item.get("latest")
    ok = bool(latest) and latest >= oldest_ok
    return {"key": key, "label": label, "dir": spec["dir"], "latest": latest, "expected": oldest_ok,
            "max_age_days": red_days, "file_count": item.get("file_count"), "ok": ok,
            "reason": "" if ok else (f"{label} 最近更新 {_mmdd(latest)}，应不早于 {_mmdd(oldest_ok)}"
                                     f"（超过 {red_days} 天没更新）" if latest else f"{label} 本地没有文件")}


class ReadinessGate:
    """一次出票（同一 as_of）里共用的数据集检查结果；每个数据集只查一次。"""

    def __init__(self, db, as_of: str, *, now: _dt.datetime | None = None, data_dir: Path | None = None):
        self.db = db
        self.as_of = as_of
        self.now = now
        self.data_dir = data_dir
        self._open_days: list[str] | None = None
        self._cache: dict[tuple[str, int], dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}

    def _dataset(self, key: str, allowed: int) -> dict[str, Any]:
        ck = (key, 0 if key in _DIR_KEYS else allowed)
        if ck not in self._cache:
            if key in _DIR_KEYS:
                self._cache[ck] = check_dir_dataset(key, self.as_of, now=self.now, base=self.data_dir)
            else:
                if self._open_days is None:
                    self._open_days = _open_days_with(self.db, self.as_of)
                self._cache[ck] = check_db_dataset(self.db, key, self.as_of, allowed, self._open_days)
        return self._cache[ck]

    def check(self, strategy_id: str, horizon: str = "") -> dict[str, Any]:
        items = [self._dataset(k, lag) for k, lag in deps_for(strategy_id)]
        missing = [i for i in items if not i["ok"]]
        res = {"strategy_id": strategy_id, "horizon": horizon, "trade_date": self.as_of, "ready": not missing,
               "items": items, "missing": missing, "reason": reason_text(missing),
               "checked_at": _dt.datetime.now(dq._CST).strftime("%Y-%m-%d %H:%M:%S")}
        self.results[strategy_id] = res
        return res


def reason_text(missing: list[dict[str, Any]]) -> str:
    return ("缺数据：" + "；".join(m["reason"] for m in missing)) if missing else ""


def blocked_raw(res: dict[str, Any]) -> dict[str, Any]:
    """写进中长线 status 文件 raw 的形状（classify_daily_status 认 data_not_ready 键）。"""
    return {STATUS: [{k: m.get(k) for k in ("key", "label", "latest", "expected", "reason")} for m in res["missing"]],
            "reason": res["reason"]}


# ---------------------------------------------------------------- 落盘 / 读取
def _store_path(trade_date: str, store_dir: Path | None = None) -> Path:
    return (store_dir or STORE_DIR) / f"{trade_date}.json"


def record(trade_date: str, results: dict[str, dict[str, Any]], store_dir: Path | None = None) -> None:
    """并入 <日期>.json（同日补跑按策略覆盖）。非致命。"""
    if not results:
        return
    try:
        p = _store_path(trade_date, store_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        stored = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        for sid, res in results.items():
            stored[sid] = {k: res.get(k) for k in ("horizon", "ready", "missing", "reason", "checked_at")}
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.warning("record strategy readiness failed: %s", exc)


def load_recorded(trade_date: str, store_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_store_path(trade_date, store_dir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 没有文件/坏文件都当没有记录
        return {}


def latest_recorded(store_dir: Path | None = None, on_or_before: str | None = None) -> tuple[str | None, dict[str, Any]]:
    d = store_dir or STORE_DIR
    if not d.exists():
        return None, {}
    for f in sorted(d.glob("????-??-??.json"), reverse=True):
        if on_or_before and f.stem > on_or_before:
            continue
        return f.stem, load_recorded(f.stem, d)
    return None, {}


def blocked_on(trade_date: str, store_dir: Path | None = None) -> dict[str, str]:
    """该日被数据闸门拦下（当前记录仍是未就绪）的策略 -> 原因。"""
    return {sid: str(r.get("reason") or "") for sid, r in load_recorded(trade_date, store_dir).items()
            if isinstance(r, dict) and r.get("ready") is False}


def alert_gate_failure(where: str, exc: BaseException) -> None:
    logger.exception("数据就绪检查本身失败（%s），本次放行不拦: %s", where, exc)
    try:
        from app.core.alerts import emit_alert

        emit_alert("strategy_data_gate_failed", where, f"出票前数据就绪检查失败、本次未拦（{where}）：{type(exc).__name__}: {str(exc)[:160]}")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 接口用：全部现役策略的依赖状态
_OVERVIEW_TTL = 60.0
_overview_cache: tuple[float, dict[str, Any]] | None = None


def overview(db, short_ids: Iterable[str], short_names: dict[str, str] | None = None,
             now: _dt.datetime | None = None, use_cache: bool = True) -> dict[str, Any]:
    """现在（按巡检的期望交易日）每个现役策略的依赖状态 + 最近一次出票时闸门的记录。"""
    global _overview_cache
    if use_cache and _overview_cache and time.monotonic() - _overview_cache[0] < _OVERVIEW_TTL:
        return _overview_cache[1]
    from app.services import midterm_service as ms

    now = now or _dt.datetime.now(dq._CST)
    expected = dq.expected_trade_date(dq.load_open_days(db), now)
    gate = ReadinessGate(db, expected, now=now)
    rec_date, rec = latest_recorded(on_or_before=now.strftime("%Y-%m-%d"))
    names = dict(short_names or {})
    rows: dict[str, Any] = {}
    sids = [(sid, "short") for sid in short_ids] + [(sid, str(m.get("horizon"))) for sid, m in ms.STRATEGIES.items()]
    for sid, hz in sids:
        live = gate.check(sid, hz)
        r = rec.get(sid) if isinstance(rec.get(sid), dict) else None
        rows[sid] = {
            "strategy_id": sid, "horizon": hz, "name": names.get(sid) or (ms.STRATEGIES.get(sid) or {}).get("name") or sid,
            "deps": [{"key": i["key"], "label": i["label"], "allowed_lag": i.get("allowed_lag"),
                      "max_age_days": i.get("max_age_days")} for i in live["items"]],
            "live": {"ready": live["ready"], "reason": live["reason"], "items": live["items"]},
            "recorded": ({"trade_date": rec_date, "ready": r.get("ready"), "reason": r.get("reason"),
                          "checked_at": r.get("checked_at")} if r else None),
        }
    out = {"expected_trade_date": expected, "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
           "recorded_trade_date": rec_date, "strategies": rows,
           "rule": "日频数据最新日期要到出票日（个别允许晚 1 个交易日）；本地财报/预告/分红历史超过 120/150 天没更新才拦。"
                   "缺数据的策略当天不出票，其它策略照常出；数据补上后补跑会正常出票。"}
    _overview_cache = (time.monotonic(), out)
    return out
