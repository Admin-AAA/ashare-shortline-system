"""现役策略「近12个月扣成本后每笔」每月滚动重算。

为什么需要：策略页的 R12（midterm_backtest_metrics.json / standalone_metrics.json）是上线时用研究脚本算的一次性快照，
窗口固定在 2025-10~2026-10。上线条件本身就要求 R12 每笔为正，不滚动重算，这个数永远是 100%，看不出策略变差。

口径（结果写清截止日 cutoff 与窗口）：
- 中线/长线：用现行生产选股代码（midterm_service.generate_picks，与实盘同一套，开每日新推荐上限）在独立 staging 目录里
  把 [窗口起点 − 预热, cutoff] 逐交易日重放一遍，不碰生产 picks 目录；收益用生产 _load_batch/_realize 按日K结算
  （次日开盘买、持有期满开盘卖、涨停不买、跌停顺延、扣 0.27% 税费加滑点）。只算信号日在窗口内、且到 cutoff 已卖出的笔。
- 短线：读库里已有的已结算推荐，只算已成交、卖出日 ≤ cutoff 的笔（净收益，没成交的不当 0% 摊）。两段不重叠、不混算：
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
# 交易时段 + 21:00 晚间补数到 21:30 出票跑完（22:15 watchdog 之后）；22:30 本任务自己的定时不在窗口内。
BLACKOUTS = ((_dt.time(9, 15), _dt.time(15, 5)), (_dt.time(21, 0), _dt.time(22, 30)))
# 短线回放（历史重建 historical_reconstruction）覆盖终点：这天及以前只用回放，之后只用实盘，两段不重叠、不混算。
# 优先从库里读（short_replay_coverage_end），读不到才用这个常量兜底：
# 2025-10-20 ~ 2026-10-16 的全量重建（scripts/rebuild_replay_picks.py，2026-10-19）。
SHORT_REPLAY_COVERAGE_END = "2026-10-16"
STOP_AT = _dt.time(0, 0)  # 22:30 开跑，到 00:00 在日边界停下，下个交易日接着跑


def month_dir(root: Path, month: str) -> Path:
    """staging 只按月份命名（rolling_r12/<YYYY-MM>/），中断后同月接着跑。"""
    return root / "rolling_r12" / month


def month_manifest(root: Path, month: str, cutoff_now: str) -> dict[str, str]:
    """本月第一次开跑时定下截止日和窗口，写进 manifest.json；之后续跑一律沿用，避免截止日漂移、重放前后不一致。"""
    f = month_dir(root, month) / "manifest.json"
    if f.exists():
        # 坏文件就地崩溃：静默按本次重新定会让截止日漂移，正是本函数要避免的前后不一致。
        return json.loads(f.read_text(encoding="utf-8"))
    end = _dt.date.fromisoformat(cutoff_now)
    man = {"month": month, "cutoff": cutoff_now,
           "window_start": (end - _dt.timedelta(days=365)).isoformat(),
           "replay_start": (end - _dt.timedelta(days=365 + WARMUP_DAYS)).isoformat()}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    return man


_LOCAL_FIELDS = ("ts_code,ann_date,end_date,currentdebt_to_debt,undist_profit_ps,netprofit_yoy,netdebt,interestdebt,"
                 "profit_dedt,or_yoy,dt_netprofit_yoy").split(",")


def _compact(day: str | None) -> str:
    return (day or "").replace("-", "")


def _covered_through(name: str, df, base: Path) -> str:
    """本地库覆盖到哪天（YYYYMMDD；as_of ≤ 这天才读本地）。

    原来直接取 ann_date 列的字符串最大值：空公告日 astype(str) 成 "None"/"nan"，按字符串比比任何日期都大，
    本地会被当成覆盖到无穷远，之后每个重放日都读本地、静默少票；部分股票刷新了、其余没刷新时，最大公告日
    也会前移，把半新半旧的库当完整。现在：只认 8 位数字公告日；fina 有刷新清单（local_fina_refresh 全部
    报告期都成功才前移 covered_through，即「全市场按期拉取的时点」）时以清单为准。"""
    s = df["ann_date"] if "ann_date" in df else None
    valid = s[s.str.fullmatch(r"\d{8}", na=False)] if s is not None else []
    mx = str(valid.max()) if len(valid) else ""
    if name == "fina":
        from app.services.local_fina_refresh import load_manifest

        ct = str(load_manifest(base / "fina").get("covered_through") or "")
        if len(ct) == 8 and ct.isdigit():
            return ct
    return mx


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
                # 列取「重放要用的全部财务字段」（FINA_FIELDS）∪ 基础字段，文件里没有的列跳过；
                # 只读基础 11 列会让用别的财务指标的策略（如销售费用率 saleexp_to_gr）取到缺列的表而抛 KeyError。
                import pyarrow.parquet as pq

                from app.services.midterm_fina_events import FINA_FIELDS

                wanted = list(dict.fromkeys(_LOCAL_FIELDS + FINA_FIELDS.split(",")))
                parts = []
                for p in sorted((base / "fina").glob("*.parquet")):
                    have = set(pq.read_schema(p).names)
                    parts.append(pd.read_parquet(p, columns=[c for c in wanted if c in have]))
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
                state["covered"][name] = _covered_through(name, df, base)
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
                cols = str(params.get("fields") or ",".join(_LOCAL_FIELDS)).split(",")
                if any(c not in df for c in cols):
                    return None  # 本地缺所请求的列：回落网关，不悄悄返回少列的表让下游 KeyError
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


def find_unavailable_strategies(no_data: dict, midlong: dict, window_days: int) -> dict[str, int]:
    """整个回放窗口都取不到数的现役策略 -> {sid: 失败天数}，其近 12 个月指标完全不可得。

    trade_stats 在 n=0 时也返回 insufficient_sample，与「有 12 笔但不到 30 笔」同标签，
    会把回放通道缺陷伪装成样本偏少。已标 error（选股抛异常）的策略以 error 为准。
    window_days 取 replay['window_days']（replay_midlong 用完整交易日列表算的），
    不能用 replay['days']——跨夜续跑时后者只是最后一次运行的天数。
    """
    if not window_days:
        return {}
    return {sid: len(set(days)) for sid, days in (no_data or {}).items()
            if days and len(set(days)) >= window_days
            and sid in midlong and midlong[sid].get("verdict") != "error"}


def midlong_window_stats(db, staging: Path, window_start: str, cutoff: str) -> dict[str, dict[str, Any]]:
    """只算信号日在 [window_start, cutoff]、且卖出日 ≤ cutoff 的笔。

    _load_batch 按库里最新日K结算，重算时已经过了 cutoff 好几天，卖出日在 cutoff 之后的笔也会是 closed；
    这些笔到截止日还在持有，算作 open，不进每笔收益（2026-11 PR #56 审查发现原来混进去了）。
    它们的收益单列在 sold_after_cutoff，只展示、不判正负。"""
    from app.services import midterm_service as ms

    rets: dict[str, list[float]] = {sid: [] for sid in ms.STRATEGIES}
    late: dict[str, list[float]] = {sid: [] for sid in ms.STRATEGIES}
    opened: dict[str, int] = {sid: 0 for sid in ms.STRATEGIES}
    for f in sorted(staging.glob("*.json")):
        b = ms._load_batch(db, f)
        sid = b["strategy_id"]
        if sid not in rets or not (window_start <= b["signal_date"] <= cutoff):
            continue
        for p in b["picks"]:
            if p.get("status") == "closed" and p.get("ret") is not None:
                if str(p.get("exit_date") or "")[:10] <= cutoff:
                    rets[sid].append(float(p["ret"]))
                else:
                    late[sid].append(float(p["ret"]))
                    opened[sid] += 1
            elif p.get("status") in ("holding", "pending"):
                opened[sid] += 1
    out = {}
    for sid, rr in rets.items():
        lt = late[sid]
        out[sid] = {"name": ms.STRATEGIES[sid].get("name", sid), "horizon": ms.STRATEGIES[sid].get("horizon"),
                    "hold_days": ms._hold(sid), **trade_stats(rr, opened[sid]),
                    "sold_after_cutoff": {"n": len(lt), "avg": round(sum(lt) / len(lt), 2) if lt else None}}
    return out


def short_segment_returns(db, seg: dict[str, Any] | None, cutoff: str,
                          expected) -> dict[str, tuple[list[float], list[float]]]:
    """短线一段里各策略已成交的净收益，按卖出日分两组：(卖出日 ≤ cutoff, 截止日后才卖出)。

    人群（去重、正式样本、回放 cohort）和每笔净收益与 outcome_service.metrics_by_strategy 完全一样，只多一道卖出日过滤。
    原来直接用 metrics_by_strategy 按信号日取：重算时已过了 cutoff 好几天，最后 1~3 个信号日在截止日后才卖出的笔
    也被算进来了（2026-11 PR #57 审查发现，中长线已在 PR #57 改掉）。没卖出日的笔不能确认在截止日前卖出，算后一组。"""
    from app.services import outcome_service as os_

    out: dict[str, tuple[list[float], list[float]]] = {}
    if seg is None:
        return out
    for rec in os_._scoped_recommendation_rows(db, seg["start"], seg["end"], sample_scope=seg["scope"]):
        if os_.normalized_outcome_label(rec) in (os_.OUTCOME_PENDING, os_.OUTCOME_PROVISIONAL):
            continue
        m = os_._metric_record_from_row(rec)
        if not m["executed"]:
            continue
        sold = str(getattr(rec, "exit_date", "") or "")[:10]
        group = 0 if sold and sold <= cutoff else 1
        for signal in os_._loads(rec.skill_breakdown_json, []):
            sid = str(signal.get("strategy_id") or "unknown")
            if sid in expected:
                out.setdefault(sid, ([], []))[group].append(float(m["net_return_pct"]))
    return out


def short_trade_rows(db, window_start: str, cutoff: str) -> list[dict[str, Any]]:
    """短线逐笔（供长远目标 goal_metrics）：每条现役短线策略按自己的回放覆盖终点切段（L-225），
    回放段只用 historical_replay、实盘段只用 live_forward，已成交、卖出日 ≤ cutoff 的笔。
    返回 {strategy_id, signal_date, code, net, bench, scope}；bench = EWALL.A 同信号日、同实际持有日数、同成本。"""
    from app.engine.trading_costs import TOTAL_ROUND_TRIP_COST_PCT
    from app.services import outcome_service as os_
    from app.services.daily_output_check import expected_short_strategies
    from app.services.market_benchmark import equal_weight_curve, holding_benchmark_return

    expected = set(expected_short_strategies(db))
    boundary, _src = short_replay_coverage_end(db)
    per_sid = short_replay_coverage_end_by_strategy(db)
    dates, pct = equal_weight_curve(db)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for sid in sorted(expected):
        segments = short_segments(window_start, cutoff, per_sid.get(sid, boundary))
        for seg in (segments["replay"], segments["live"]):
            if seg is None:
                continue
            for rec in os_._scoped_recommendation_rows(db, seg["start"], seg["end"], sample_scope=seg["scope"]):
                if os_.normalized_outcome_label(rec) in (os_.OUTCOME_PENDING, os_.OUTCOME_PROVISIONAL):
                    continue
                m = os_._metric_record_from_row(rec)
                if not m["executed"]:
                    continue
                sold = str(getattr(rec, "exit_date", "") or "")[:10]
                if not sold or sold > cutoff:
                    continue
                if sid not in {str(x.get("strategy_id")) for x in os_._loads(rec.skill_breakdown_json, [])}:
                    continue
                sd, code = str(rec.trade_date)[:10], str(rec.code)
                if (sid, sd, code) in seen:
                    continue
                seen.add((sid, sd, code))
                hold = max(1, int(round(m["holding_days"] or 1)))
                b = holding_benchmark_return(dates, pct, sd, hold, cost_pct=TOTAL_ROUND_TRIP_COST_PCT)
                if b is None:
                    continue
                out.append({"strategy_id": sid, "signal_date": sd, "code": code, "net": float(m["net_return_pct"]),
                            "bench": b, "scope": seg["scope"]})
    return out


def _seg_summary(seg: dict[str, Any] | None, rets: list[float], late: list[float]) -> dict[str, Any]:
    return {**(seg or {}), "n": len(rets), "avg": round(sum(rets) / len(rets), 2) if rets else None,
            "sold_after_cutoff": {"n": len(late), "avg": round(sum(late) / len(late), 2) if late else None}}


def short_window_stats(db, window_start: str, cutoff: str) -> dict[str, Any]:
    """短线：只算信号日在窗口内、卖出日 ≤ cutoff 的已成交笔（与中长线同口径）；
    截止日后才卖出的算持有中（open），收益按段单列 sold_after_cutoff，只展示、不判正负。"""
    from app.services.daily_output_check import expected_short_strategies

    expected = expected_short_strategies(db)
    boundary, boundary_source = short_replay_coverage_end(db)
    per_sid = short_replay_coverage_end_by_strategy(db)
    out: dict[str, Any] = {}
    for sid in expected:
        # 每个策略用自己最新已验证回放批次的终点切段：策略 yaml 改版后只重建该策略时，
        # 别的策略的实盘段不能被全局 MAX 吞掉（L-225）。没有自己批次的策略退回全局终点。
        b = per_sid.get(sid, boundary)
        segments = short_segments(window_start, cutoff, b)
        r_in, r_late = short_segment_returns(db, segments["replay"], cutoff, [sid]).get(sid, ([], []))
        l_in, l_late = short_segment_returns(db, segments["live"], cutoff, [sid]).get(sid, ([], []))
        tot = r_in + l_in
        avg = sum(tot) / len(tot) if tot else None
        verdict = "insufficient_sample" if len(tot) < MIN_TRADES else ("positive" if (avg or 0) > 0 else "negative")
        out[sid] = {
            "n": len(tot), "avg": round(avg, 2) if avg is not None else None, "verdict": verdict,
            "open": len(r_late) + len(l_late),
            "replay_coverage_end": b,
            "replay_segment": _seg_summary(segments["replay"], r_in, r_late),
            "live_segment": _seg_summary(segments["live"], l_in, l_late),
        }
    return {"strategies": out, "replay_coverage_end": boundary, "replay_coverage_end_source": boundary_source,
            "replay_coverage_end_by_strategy": per_sid}


def short_replay_coverage_end(db) -> tuple[str, str]:
    """短线回放覆盖终点：优先从库里读（completed 的 historical_reconstruction 正式 run 的最大交易日），
    读不到（空表/查询失败）再用常量 SHORT_REPLAY_COVERAGE_END。返回 (日期, "data"|"constant")。"""
    try:
        v = db.execute(text(
            "SELECT MAX(trade_date) FROM recommendation_runs "
            "WHERE evidence_class='historical_reconstruction' AND status='completed' AND run_mode='formal'"
        )).scalar()
    except Exception:  # noqa: BLE001 - 读不到就用常量
        logger.warning("short replay coverage end unreadable, using constant", exc_info=True)
        v = None
    s = str(v)[:10] if v else ""
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s, "data"
    return SHORT_REPLAY_COVERAGE_END, "constant"


def short_replay_coverage_end_by_strategy(db) -> dict[str, str]:
    """每个短线策略自己的回放覆盖终点：该策略最新已验证重建批次（含被更新批次部分覆盖后保留的旧批次）里的最大交易日。

    策略身份取 cohort 的 strategy_key（``skills:<skill_id>``，与 run_name / attempt-tag 无关）。
    读不到批次时返回空 dict，调用方退回全局终点。"""
    from app.services.evidence.reconstruction_batch import latest_validated_reconstruction_cohorts

    try:
        cohorts = latest_validated_reconstruction_cohorts(db)
    except Exception:  # noqa: BLE001 - 读不到就退回全局终点
        logger.warning("per-strategy short replay coverage end unreadable", exc_info=True)
        return {}
    out: dict[str, str] = {}
    for c in cohorts:
        key = str(c.strategy_key or "")
        if not key.startswith("skills:") or "+" in key or not c.expected_trade_dates:
            continue
        sid = key[len("skills:"):]
        end = max(c.expected_trade_dates)
        if sid not in out or end > out[sid]:
            out[sid] = end
    return out


def short_segments(window_start: str, cutoff: str, boundary: str | None = None) -> dict[str, dict[str, str] | None]:
    """短线两段互不重叠：回放覆盖终点（含）及以前只用历史重建，之后只用实盘。窗口外的段为 None。"""
    b = boundary or SHORT_REPLAY_COVERAGE_END
    replay = {"scope": "historical_replay", "start": window_start, "end": min(b, cutoff)} if window_start <= b else None
    live_start = (_dt.date.fromisoformat(b) + _dt.timedelta(days=1)).isoformat()
    live_start = max(live_start, window_start)
    live = {"scope": "live_forward", "start": live_start, "end": cutoff} if live_start <= cutoff else None
    return {"replay": replay, "live": live}


# 结果里有「整天取数失败」的策略时，本月不再算完成：否则月度任务按「本月已算过」一直 skip，
# 缺的日子永远补不上，数据质量巡检的 formal:rolling_r12 黄项会永久挂着
# （2026-11-23 体检：mid-incentive-option / mid-incentive-vol 各 271 天不完整，结果文件停在 11-02）。
# 但全量重放一次约 4 小时，不能每天跑：按周重试一次，补齐后 incomplete_days 自然清空。
_INCOMPLETE_RETRY_HOURS = 24 * 7


def _month_done(month: str) -> bool:
    try:
        d = json.loads(LATEST.read_text())
    except Exception:  # noqa: BLE001
        return False
    if not (d.get("month") == month and d.get("status") == "completed"):
        return False
    incomplete = (d.get("replay") or {}).get("incomplete_days") or {}
    if not incomplete:
        return True
    try:
        gen = _dt.datetime.fromisoformat(str(d.get("generated_at")))
    except (TypeError, ValueError):
        return True
    return _now() - gen < _dt.timedelta(hours=_INCOMPLETE_RETRY_HOURS)


def verdict_summary(midlong: dict[str, Any], short: dict[str, Any]) -> dict[str, Any]:
    """counts 与 positive_ratio：分母只算可判定的（为正/为负），样本不足、抛异常、指标不可得不进分母。"""
    allv = [v["verdict"] for v in midlong.values()] + [v["verdict"] for v in short["strategies"].values()]
    judged = [v for v in allv if v not in ("insufficient_sample", "error", "unavailable")]
    return {"positive_ratio": round(judged.count("positive") / len(judged), 3) if judged else None,
            "counts": {"positive": allv.count("positive"), "negative": allv.count("negative"),
                       "insufficient_sample": allv.count("insufficient_sample"), "error": allv.count("error"),
                       "unavailable": allv.count("unavailable")}}


def method_text(short: dict[str, Any]) -> str:
    return ("中长线：现行生产选股代码重放（开每日上限），财报/分红/回购/业绩预告/股权激励草案优先读本地库与事件存档，本地未覆盖的日期才走网关；"
            "只算窗口内信号、卖出日 ≤ 截止日的笔，扣 0.27%（税费 0.07% + 滑点 0.20%）；截止日后才卖出的笔算持有中，收益另列「最近已卖出」（sold_after_cutoff）作预警，不进正负判定。"
            f"短线：{short['replay_coverage_end']}（来源 {short['replay_coverage_end_source']}）及以前只用历史重建、"
            "之后只用实盘，两段分开列，已成交且卖出日 ≤ 截止日的净收益（未成交不摊 0%），截止日后才卖出的按段另列 sold_after_cutoff。"
            "样本 < 30 笔不判正负")


def run_monthly(db, *, force: bool = False, now: _dt.datetime | None = None,
                out_root: Path | None = None) -> dict[str, Any]:
    from app.services import midterm_service as ms

    now = now or _now()
    if in_blackout(now) and not force:
        return {"status": "skipped", "summary_text": "交易时段 / 21:00–22:30 晚间补数与出票窗口内不跑滚动重算"}
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
                                apply_daily_cap=True, retry_waits=(60,), isolate_errors=True)
    if replay.get("stopped"):
        # 计划内停下（00:00），不是失败：任务记 completed，下个交易日 22:30 按 manifest 同一截止日接着跑
        return {"status": "completed", "finished": False, "cutoff": cutoff,
                "replay": {k: replay.get(k) for k in ("days", "done_through", "seconds")},
                "upstream_calls": dict(upstream["calls"]),
                "summary_text": f"中长线重放到 {replay.get('done_through')}，到 00:00 停下，下个交易日接着跑（截止日 {cutoff} 不变）"}
    midlong = midlong_window_stats(db, staging, window_start, cutoff)
    # 重放中抛异常被跳过的策略：这条数据不完整，不能拿残缺笔数判正负 -> 标 error，不进"为正/为负"，单独告警，其余照算
    crashed = {sid: c for sid, c in (replay.get("crashed") or {}).items() if sid in midlong}
    for sid, c in crashed.items():
        midlong[sid] = {**midlong[sid], "verdict": "error", "error": c.get("error"),
                        "error_first_day": c.get("first_day"), "error_days": c.get("days")}
    short = short_window_stats(db, window_start, cutoff)
    # 「整个回放窗口都取不到数」不是「样本不足」：前者是该策略近 12 个月指标完全不可得，
    # 后者只是笔数没到 30。trade_stats 在 n=0 时也返回 insufficient_sample，混在一起
    # 会让人误以为只是样本偏少，掩盖回放数据通道缺陷（激励策略曾这样空了 271 天）。
    # window_days 由 replay_midlong 用完整交易日列表给出；这里不再查库（R12 单测用 fake db）。
    window_days = int(replay.get("window_days") or 0)
    no_data = replay.get("no_data") or {}
    unavailable = find_unavailable_strategies(no_data, midlong, window_days)
    for sid, n in unavailable.items():
        midlong[sid] = {**midlong[sid], "verdict": "unavailable", "unavailable_days": n}
    incomplete = {k: v for k, v in no_data.items() if v} | \
                 {k: v for k, v in (replay.get("errors") or {}).items() if v}
    doc = {
        "month": month,
        "cutoff": cutoff,
        "window": {"signal_start": window_start, "signal_end": cutoff, "replay_start": replay_start},
        "generated_at": now.isoformat(timespec="seconds"),
        "status": "completed",
        **verdict_summary(midlong, short),
        "crashed": {sid: {k: c.get(k) for k in ("error", "first_day", "days")} for sid, c in crashed.items()},
        "midlong": midlong,
        "short": short,
        "replay": {"days": replay.get("days"), "window_days": window_days, "seconds": replay.get("seconds"),
                   "incomplete_days": {k: len(v) for k, v in incomplete.items()},
                   "unavailable_strategies": sorted(unavailable),
                   "upstream_calls": dict(upstream["calls"]), "local_served": dict(upstream["local"]),
                   "local_covered_through": upstream["covered"]},
        "method": method_text(short),
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
            from app.services.honest_alerts import emit_daily

            emit_daily("rolling_r12_negative", cutoff, f"近12个月（截止 {cutoff}）每笔转负的现役策略：{','.join(neg)}")
        if incomplete:
            emit_alert("rolling_r12_incomplete", cutoff, f"近12个月滚动重算（截止 {cutoff}）有取数失败日：{doc['replay']['incomplete_days']}")
        if crashed:
            emit_alert("rolling_r12_incomplete", f"{cutoff}:crashed",
                       f"近12个月滚动重算（截止 {cutoff}）有策略选股抛异常、已跳过（其余照算）："
                       + "；".join(f"{sid}（{c.get('error')}）" for sid, c in crashed.items()))
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
                         f"样本不足 {doc['counts']['insufficient_sample']}"
                         + (f"、指标不可得 {len(unavailable)}（{','.join(sorted(unavailable))}）" if unavailable else "")
                         + (f"、选股抛异常已跳过 {len(crashed)}（{','.join(crashed)}）" if crashed else "")
                         + (f"；转负：{','.join(neg)}" if neg else "")
                         + (f"；有取数失败日：{doc['replay']['incomplete_days']}" if incomplete else "")),
    }


def load_latest() -> dict[str, Any] | None:
    try:
        return json.loads(LATEST.read_text())
    except Exception:  # noqa: BLE001
        return None
