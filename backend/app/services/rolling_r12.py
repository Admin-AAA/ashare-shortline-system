"""现役策略「近12个月扣成本后每笔」每月滚动重算。

为什么需要：策略页的 R12（midterm_backtest_metrics.json / standalone_metrics.json）是上线时用研究脚本算的一次性快照，
窗口固定在 2025-10~2026-10。上线条件本身就要求 R12 每笔为正，不滚动重算，这个数永远是 100%，看不出策略变差。

口径（结果写清截止日 cutoff 与窗口）：
- 中线/长线：用现行生产选股代码（midterm_service.generate_picks，与实盘同一套，开每日新推荐上限）在独立 staging 目录里
  把 [窗口起点 − 预热, cutoff] 逐交易日重放一遍，不碰生产 picks 目录；收益用生产 _load_batch/_realize 按日K结算
  （次日开盘买、持有期满开盘卖、涨停不买、跌停顺延、扣 0.3%）。只算信号日在窗口内、且到 cutoff 已卖出的笔。
- 短线：读库里已有的已结算推荐，只算已成交的笔（净收益，没成交的不当 0% 摊）。两段不重叠、不混算：
  SHORT_REPLAY_COVERAGE_END（回放覆盖终点）及以前只用历史重建（historical_replay），之后只用实盘（live_forward），
  输出里两段分开列笔数和每笔。不另跑回放。
- 重放尽量只读本地库（local_first），本地覆盖不到的日期才走网关，每个接口走了几次上游写进结果。
- staging 只按月份命名，manifest 固定本月截止日；到 00:00 在日边界停下，下个交易日接着跑。
- 样本少于 30 笔标 insufficient_sample，不算"为正"也不算"为负"。

输出：backend/output/strategy_health/rolling_r12_latest.json（另存一份 rolling_r12_<cutoff>.json 归档）。
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import logging
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any, Iterator

from sqlalchemy import text

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = BACKEND_ROOT / "output" / "strategy_health"
LATEST = OUT_ROOT / "rolling_r12_latest.json"
MIN_TRADES = 30
WARMUP_DAYS = 100  # 自然日，约 70 个交易日，覆盖长线 60 日持有，让窗口开头的「已持有不重复买」与实盘一致
BLACKOUTS = ((_dt.time(9, 15), _dt.time(15, 5)), (_dt.time(16, 35), _dt.time(18, 35)))
# 短线回放（历史重建 historical_reconstruction）覆盖终点：这天及以前只用回放，之后只用实盘，两段不重叠、不混算。
# 2025-10-20 ~ 2026-10-16 的全量重建（scripts/rebuild_replay_picks.py，2026-10-19）。以后扩展重建范围时改这里。
SHORT_REPLAY_COVERAGE_END = "2026-10-16"
STOP_AT = _dt.time(0, 0)  # 22:30 开跑，到 00:00 在日边界停下，下个交易日接着跑


def month_dir(root: Path, month: str) -> Path:
    """staging 只按月份命名（rolling_r12/<YYYY-MM>/），中断后同月接着跑。"""
    return root / "rolling_r12" / month


def month_manifest(root: Path, month: str, cutoff_now: str) -> dict[str, str]:
    """本月第一次开跑时定下截止日和窗口，写进 manifest.json；之后续跑一律沿用，避免截止日漂移、重放前后不一致。"""
    f = month_dir(root, month) / "manifest.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:  # noqa: BLE001 - 坏文件就按本次重新定
            pass
    end = _dt.date.fromisoformat(cutoff_now)
    man = {"month": month, "cutoff": cutoff_now,
           "window_start": (end - _dt.timedelta(days=365)).isoformat(),
           "replay_start": (end - _dt.timedelta(days=365 + WARMUP_DAYS)).isoformat()}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(man, ensure_ascii=False, indent=1))
    return man


_LOCAL_FIELDS = ("ts_code,ann_date,end_date,currentdebt_to_debt,undist_profit_ps,netprofit_yoy,netdebt,interestdebt,"
                 "profit_dedt,or_yoy,dt_netprofit_yoy").split(",")


def _compact(day: str | None) -> str:
    return (day or "").replace("-", "")


@contextlib.contextmanager
def local_first(data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    """重放期间优先读本地库，本地覆盖不到当前重放日（as_of）时才走网关；统计每个接口走了几次上游。

    本地：data/midterm/fina（逐股财务指标）、dividend（逐股分红）、events/repurchase_*（回购公告）。
    业绩预告由 replay_midlong 自己换成本地读。大宗交易（block_trade）本地没有，只能走网关（会被计数）。"""
    import pandas as pd

    from app.services import midterm_service as ms
    from app.services import tushare_client as tc

    base = data_dir or (BACKEND_ROOT / "data" / "midterm")
    state: dict[str, Any] = {"as_of": None, "calls": Counter(), "local": Counter(), "covered": {}}
    cache: dict[str, Any] = {}

    def table(name: str) -> pd.DataFrame | None:
        if name in cache:
            return cache[name]
        df = None
        try:
            if name == "fina":
                parts = [pd.read_parquet(p, columns=_LOCAL_FIELDS) for p in sorted((base / "fina").glob("*.parquet"))]
            elif name == "dividend":
                parts = [pd.read_parquet(p, columns=["ts_code", "end_date", "ann_date", "div_proc", "cash_div_tax"])
                         for p in sorted((base / "dividend").glob("*.parquet"))]
            else:
                parts = [pd.read_parquet(p) for p in sorted((base / "events").glob("repurchase_*.parquet"))]
            parts = [p for p in parts if len(p)]
            if parts:
                df = pd.concat(parts, ignore_index=True)
                for k in ("ts_code", "ann_date", "end_date"):
                    if k in df:
                        df[k] = df[k].astype(str).str.strip()
                state["covered"][name] = str(df["ann_date"].max())
        except Exception:  # noqa: BLE001 - 本地读失败就全部走网关（会被计数）
            logger.warning("local %s store unreadable, falling back to gateway", name, exc_info=True)
            df = None
        cache[name] = df
        return df

    def covers(name: str) -> pd.DataFrame | None:
        df = table(name)
        if df is None or not state["as_of"]:
            return None
        return df if state["covered"].get(name, "") >= _compact(state["as_of"]) else None

    def local_fetch(api: str, params: dict[str, Any]):
        if api == "fina_indicator_vip" and "period" in params:
            df = covers("fina")
            if df is not None:
                cols = [c for c in str(params.get("fields") or ",".join(_LOCAL_FIELDS)).split(",") if c in df]
                return df.loc[df["end_date"] == str(params["period"]), cols].reset_index(drop=True)
        if api == "dividend":
            df = covers("dividend")
            if df is not None and "ann_date" in params:
                return df[df["ann_date"] == str(params["ann_date"])].reset_index(drop=True)
            if df is not None and "ts_code" in params:
                return df[df["ts_code"] == str(params["ts_code"])].reset_index(drop=True)
        if api == "repurchase" and "start_date" in params:
            df = covers("repurchase")
            if df is not None:
                return df[(df["ann_date"] >= str(params["start_date"]))
                          & (df["ann_date"] <= str(params["end_date"]))].reset_index(drop=True)
        return None

    orig_gen, orig_fetch, orig_ffp, orig_get_pro = ms.generate_picks, ms._pro_fetch_with_wait, ms._fetch_fina_period, tc.get_pro

    def gen(db, as_of, *a, **k):
        state["as_of"] = as_of
        return orig_gen(db, as_of, *a, **k)

    def fetch(api, label, *, alt_params=None, **params):
        hit = local_fetch(api, params)
        if hit is not None:
            state["local"][api] += 1
            return hit
        return orig_fetch(api, label, alt_params=alt_params, **params)

    def ffp(period):
        hit = local_fetch("fina_indicator_vip", {"period": period,
                                                 "fields": "ts_code,ann_date,end_date,dt_netprofit_yoy,or_yoy,profit_dedt"})
        if hit is not None:
            state["local"]["fina_indicator_vip"] += 1
            return hit
        return orig_ffp(period)

    class _CountingPro:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            attr = getattr(self._inner, name)
            if not callable(attr):
                return attr

            def call(*a, **k):
                state["calls"][name] += 1
                return attr(*a, **k)
            return call

    ms.generate_picks, ms._pro_fetch_with_wait, ms._fetch_fina_period = gen, fetch, ffp
    tc.get_pro = lambda *a, **k: _CountingPro(orig_get_pro(*a, **k))
    try:
        yield state
    finally:
        ms.generate_picks, ms._pro_fetch_with_wait, ms._fetch_fina_period = orig_gen, orig_fetch, orig_ffp
        tc.get_pro = orig_get_pro


def _now() -> _dt.datetime:
    from app.services.midterm_live_seal import now_cn

    return now_cn().replace(tzinfo=None)


def in_blackout(now: _dt.datetime) -> bool:
    t = now.time()
    return any(a <= t < b for a, b in BLACKOUTS)


def _latest_trade_date(db) -> str:
    return str(db.execute(text("SELECT MAX(trade_date) FROM daily_kbars WHERE code='000001'")).scalar())


def trade_stats(rets: list[float], open_count: int = 0) -> dict[str, Any]:
    n = len(rets)
    if not n:
        return {"n": 0, "avg": None, "median": None, "win_rate": None, "pf": None, "trim5_avg": None,
                "open": open_count, "verdict": "insufficient_sample"}
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    srt = sorted(rets)
    k = int(n * 0.05)
    core = srt[k:n - k] if n - 2 * k > 0 else srt
    avg = sum(rets) / n
    verdict = "insufficient_sample" if n < MIN_TRADES else ("positive" if avg > 0 else "negative")
    return {
        "n": n, "avg": round(avg, 2), "median": round(median(rets), 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "pf": round(sum(wins) / abs(sum(losses)), 2) if wins and losses else None,
        "trim5_avg": round(sum(core) / len(core), 2), "open": open_count, "verdict": verdict,
    }


def midlong_window_stats(db, staging: Path, window_start: str, cutoff: str) -> dict[str, dict[str, Any]]:
    from app.services import midterm_service as ms

    rets: dict[str, list[float]] = {sid: [] for sid in ms.STRATEGIES}
    opened: dict[str, int] = {sid: 0 for sid in ms.STRATEGIES}
    for f in sorted(staging.glob("*.json")):
        b = ms._load_batch(db, f)
        sid = b["strategy_id"]
        if sid not in rets or not (window_start <= b["signal_date"] <= cutoff):
            continue
        for p in b["picks"]:
            if p.get("status") == "closed" and p.get("ret") is not None:
                rets[sid].append(float(p["ret"]))
            elif p.get("status") in ("holding", "pending"):
                opened[sid] += 1
    out = {}
    for sid, rr in rets.items():
        out[sid] = {"name": ms.STRATEGIES[sid].get("name", sid), "horizon": ms.STRATEGIES[sid].get("horizon"),
                    "hold_days": ms._hold(sid), **trade_stats(rr, opened[sid])}
    return out


def short_window_stats(db, window_start: str, cutoff: str) -> dict[str, Any]:
    from app.services.daily_output_check import expected_short_strategies
    from app.services.outcome_service import metrics_by_strategy

    expected = expected_short_strategies(db)
    segments = short_segments(window_start, cutoff)

    def seg_rows(seg: dict[str, Any] | None) -> dict[str, tuple[int, float]]:
        if seg is None:
            return {}
        out_: dict[str, tuple[int, float]] = {}
        for row in metrics_by_strategy(db, start_date=seg["start"], end_date=seg["end"], sample_scope=seg["scope"]):
            sid = row.get("strategy_id")
            n = int(row.get("triggered_count") or 0)
            if sid in expected and n:
                out_[sid] = (n, float(row.get("avg_return") or 0.0))
        return out_

    replay_rows, live_rows = seg_rows(segments["replay"]), seg_rows(segments["live"])
    actual_cover = db.execute(text(
        "SELECT MAX(trade_date) FROM recommendation_runs WHERE evidence_class='historical_reconstruction' AND status='completed'"
    )).scalar()
    out: dict[str, Any] = {}
    for sid in expected:
        rn, ra = replay_rows.get(sid, (0, 0.0))
        ln, la = live_rows.get(sid, (0, 0.0))
        tot_n = rn + ln
        avg = (rn * ra + ln * la) / tot_n if tot_n else None
        verdict = "insufficient_sample" if tot_n < MIN_TRADES else ("positive" if (avg or 0) > 0 else "negative")
        out[sid] = {
            "n": tot_n, "avg": round(avg, 2) if avg is not None else None, "verdict": verdict,
            "replay_segment": {**(segments["replay"] or {}), "n": rn, "avg": round(ra, 2) if rn else None},
            "live_segment": {**(segments["live"] or {}), "n": ln, "avg": round(la, 2) if ln else None},
        }
    return {"strategies": out, "replay_coverage_end": SHORT_REPLAY_COVERAGE_END,
            "db_reconstruction_max_date": str(actual_cover) if actual_cover else None}


def short_segments(window_start: str, cutoff: str, boundary: str | None = None) -> dict[str, dict[str, str] | None]:
    """短线两段互不重叠：回放覆盖终点（含）及以前只用历史重建，之后只用实盘。窗口外的段为 None。"""
    b = boundary or SHORT_REPLAY_COVERAGE_END
    replay = {"scope": "historical_replay", "start": window_start, "end": min(b, cutoff)} if window_start <= b else None
    live_start = (_dt.date.fromisoformat(b) + _dt.timedelta(days=1)).isoformat()
    live_start = max(live_start, window_start)
    live = {"scope": "live_forward", "start": live_start, "end": cutoff} if live_start <= cutoff else None
    return {"replay": replay, "live": live}


def _month_done(month: str) -> bool:
    try:
        d = json.loads(LATEST.read_text())
    except Exception:  # noqa: BLE001
        return False
    return d.get("month") == month and d.get("status") == "completed"


def run_monthly(db, *, force: bool = False, now: _dt.datetime | None = None,
                out_root: Path | None = None) -> dict[str, Any]:
    from app.services import midterm_service as ms

    now = now or _now()
    if in_blackout(now) and not force:
        return {"status": "skipped", "summary_text": "交易时段 / 盘后日更窗口内不跑滚动重算"}
    cutoff = _latest_trade_date(db)
    month = now.strftime("%Y-%m")
    root = out_root or OUT_ROOT
    if not force and out_root is None and _month_done(month):
        return {"status": "skipped", "summary_text": f"{month} 已算过，本月不再重算"}
    man = month_manifest(root, month, cutoff)
    cutoff, window_start, replay_start = man["cutoff"], man["window_start"], man["replay_start"]
    staging = month_dir(root, month) / "picks"

    from scripts.rebuild_replay_picks import replay_midlong

    with local_first() as upstream:
        replay = replay_midlong(db, replay_start, cutoff, list(ms.STRATEGIES), staging, stop_at=STOP_AT,
                                apply_daily_cap=True, retry_waits=(60,))
    if replay.get("stopped"):
        # 计划内停下（00:00），不是失败：任务记 completed，下个交易日 22:30 按 manifest 同一截止日接着跑
        return {"status": "completed", "finished": False, "cutoff": cutoff,
                "replay": {k: replay.get(k) for k in ("days", "done_through", "seconds")},
                "upstream_calls": dict(upstream["calls"]),
                "summary_text": f"中长线重放到 {replay.get('done_through')}，到 00:00 停下，下个交易日接着跑（截止日 {cutoff} 不变）"}
    midlong = midlong_window_stats(db, staging, window_start, cutoff)
    short = short_window_stats(db, window_start, cutoff)
    allv = [v["verdict"] for v in midlong.values()] + [v["verdict"] for v in short["strategies"].values()]
    judged = [v for v in allv if v != "insufficient_sample"]
    incomplete = {k: v for k, v in (replay.get("no_data") or {}).items() if v} | \
                 {k: v for k, v in (replay.get("errors") or {}).items() if v}
    doc = {
        "month": month,
        "cutoff": cutoff,
        "window": {"signal_start": window_start, "signal_end": cutoff, "replay_start": replay_start},
        "generated_at": now.isoformat(timespec="seconds"),
        "status": "completed",
        "positive_ratio": round(judged.count("positive") / len(judged), 3) if judged else None,
        "counts": {"positive": allv.count("positive"), "negative": allv.count("negative"),
                   "insufficient_sample": allv.count("insufficient_sample")},
        "midlong": midlong,
        "short": short,
        "replay": {"days": replay.get("days"), "seconds": replay.get("seconds"),
                   "incomplete_days": {k: len(v) for k, v in incomplete.items()},
                   "upstream_calls": dict(upstream["calls"]), "local_served": dict(upstream["local"]),
                   "local_covered_through": upstream["covered"]},
        "method": ("中长线：现行生产选股代码重放（开每日上限），财报/分红/回购/业绩预告优先读本地库，本地未覆盖的日期才走网关；"
                   "只算窗口内信号、截止日前已卖出的笔，扣 0.3%。"
                   f"短线：{SHORT_REPLAY_COVERAGE_END} 及以前只用历史重建、之后只用实盘，两段分开列，已成交净收益（未成交不摊 0%）。"
                   "样本 < 30 笔不判正负"),
    }
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=1)
    (root / f"rolling_r12_{cutoff}.json").write_text(payload, encoding="utf-8")
    if out_root is None:
        LATEST.write_text(payload, encoding="utf-8")
    neg = [sid for sid, v in {**midlong, **short["strategies"]}.items() if v["verdict"] == "negative"]
    try:
        from app.core.alerts import emit_alert

        if neg:
            emit_alert("rolling_r12_negative", cutoff, f"近12个月（截止 {cutoff}）每笔转负的现役策略：{','.join(neg)}")
        if incomplete:
            emit_alert("rolling_r12_incomplete", cutoff, f"近12个月滚动重算（截止 {cutoff}）有取数失败日：{doc['replay']['incomplete_days']}")
    except Exception:  # noqa: BLE001
        logger.warning("rolling r12 alert failed", exc_info=True)
    # 任务本身跑完就是 completed；策略转负 / 取数失败是业务结果，写进摘要并单独告警，不让任务失败去扣健康分
    return {
        "status": "completed",
        "finished": True,
        "cutoff": cutoff,
        "counts": doc["counts"],
        "positive_ratio": doc["positive_ratio"],
        "negative": neg,
        "summary_text": (f"近12个月滚动重算（截止 {cutoff}）：为正 {doc['counts']['positive']}、为负 {doc['counts']['negative']}、"
                         f"样本不足 {doc['counts']['insufficient_sample']}" + (f"；转负：{','.join(neg)}" if neg else "")
                         + (f"；有取数失败日：{doc['replay']['incomplete_days']}" if incomplete else "")),
    }


def load_latest() -> dict[str, Any] | None:
    try:
        return json.loads(LATEST.read_text())
    except Exception:  # noqa: BLE001
        return None
