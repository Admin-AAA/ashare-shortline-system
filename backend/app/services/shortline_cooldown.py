"""短线策略级权益曲线冷却（Sprint4 §104，2026-10-19 采纳；仅 sector-relative-strength-leader）。

规则（预注册 C3，研究脚本 scripts/research_cooldown_filter.py）：
  E(t) = 截至交易日 t 已平仓「影子单」净收益（%，已扣费）累加；信号日 T 若 MA20(E) < MA60(E) → 当日该策略不开新仓（整日、不递补）。
  影子单 = 该策略**未过滤**的每日选股（冷却日的选股照样记为影子单，所以跳过不会冻结信号）；
  严格 PIT：信号日 T 只计 exit_date ≤ T 且出场已定（非截断的 horizon 临时平仓）的影子单。
影子单来源：
  1) 种子：12m 单策略面板 base 选股（2025-10-09~2026-10-13，data/shortline_cooldown/<sid>_seed.json，
     scripts/export_cooldown_seed.py 生成）——上线前线上单策略 run 只有 3 天；
  2) 线上 live_forward formal 单策略 run 的推荐（trade_date > 种子末日）；
  3) 冷却日被拦下的选股（output/shortline_cooldown/<sid>/<date>.json，本模块写）。
出场逐笔用与回测同一的 _simulate_card（strategy.yaml exit_overrides + ma_break_exit），日K 只取 ≤ T。
出错一律放行（不拦截），只记 warning。
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

B = Path(__file__).resolve().parents[2]
SEED_DIR = B / "data" / "shortline_cooldown"
SHADOW_DIR = B / "output" / "shortline_cooldown"

# sid -> 规则名。只放研究采纳的策略。
COOLDOWN_RULES: dict[str, str] = {"sector-relative-strength-leader": "equity_ma20_lt_ma60"}
LIVE_FROM = "2026-10-19"  # 早于此日的历史回放不受影响
LOOKBACK_TD = 130  # MA20−MA60 只依赖近 60 个交易日内平仓的影子单；130 足够覆盖入场+持有期
FAST, SLOW = 20, 60


def equity_ma_blocked(closed: Iterable[tuple[str, float]], cal: Sequence[str], t: str) -> dict[str, Any]:
    """closed: [(exit_date 'YYYY-MM-DD', net%)]；cal: 升序交易日（含 t）。返回 {active, ma_fast, ma_slow, n_closed}。

    与研究 blocked_days C3 同口径：E 从第一笔平仓日起按交易日累加；不足 SLOW 个交易日 → 不拦截。
    """
    import bisect

    if t not in cal:
        cal = sorted(set(cal) | {t})
    ti = cal.index(t)
    by_idx: dict[int, float] = {}
    n = 0
    for ex, net in closed:
        ex = str(ex)[:10]
        if ex > t:
            continue
        i = cal.index(ex) if ex in cal else bisect.bisect_right(cal, ex) - 1
        by_idx[i] = by_idx.get(i, 0.0) + float(net)
        n += 1
    if not by_idx:
        return {"active": False, "ma_fast": None, "ma_slow": None, "n_closed": 0}
    first = min(by_idx)
    E, eq = [], 0.0
    for i in range(first, ti + 1):
        eq += by_idx.get(i, 0.0)
        E.append(eq)
    if len(E) < SLOW:
        return {"active": False, "ma_fast": None, "ma_slow": None, "n_closed": n}
    mf, ms = sum(E[-FAST:]) / FAST, sum(E[-SLOW:]) / SLOW
    return {"active": mf < ms, "ma_fast": round(mf, 4), "ma_slow": round(ms, 4), "n_closed": n}


def _seed(sid: str) -> list[dict]:
    f = SEED_DIR / f"{sid}_seed.json"
    if not f.exists():
        return []
    return list((json.loads(f.read_text(encoding="utf-8")) or {}).get("picks") or [])


def _shadow_files(sid: str) -> list[dict]:
    out: list[dict] = []
    d = SHADOW_DIR / sid
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                out.extend((json.loads(f.read_text(encoding="utf-8")) or {}).get("picks") or [])
            except Exception:  # noqa: BLE001
                logger.warning("cooldown shadow file unreadable: %s", f)
    return out


def _live_picks(db, sid: str, after: str, before: str) -> list[dict]:
    from sqlalchemy import select
    from app.models.recommendation import Recommendation, RecommendationRun
    from app.services.standalone_strategies import is_run_of_strategy

    runs = db.execute(
        select(RecommendationRun.run_id, RecommendationRun.selected_skills_json).where(
            RecommendationRun.evaluation_scope == "live_forward",
            RecommendationRun.run_mode == "formal",
            RecommendationRun.trade_date > after,
            RecommendationRun.trade_date < before,
        )
    ).all()
    ids = [r.run_id for r in runs if is_run_of_strategy(r.selected_skills_json, sid)]
    if not ids:
        return []
    out = []
    for r in db.execute(select(Recommendation).where(Recommendation.run_id.in_(ids))).scalars():
        try:
            fs = json.loads(r.feature_snapshot_json or "{}")
        except (TypeError, ValueError):
            fs = {}
        out.append({"signal_date": str(r.trade_date), "code": str(r.code), "recommend_price": float(r.recommend_price or 0),
                    "entry_mode": fs.get("entry_mode") or "target_touch"})
    return out


def shadow_picks(db, sid: str, trade_date: str, start: str) -> list[dict]:
    seed = _seed(sid)
    seed_end = max((p["signal_date"] for p in seed), default="0000-00-00")
    picks = seed + _live_picks(db, sid, seed_end, trade_date) + _shadow_files(sid)
    seen, out = set(), []
    for p in sorted(picks, key=lambda p: (p["signal_date"], p["code"])):
        k = (p["signal_date"], p["code"])
        if k in seen or not (start <= p["signal_date"] < trade_date):
            continue
        seen.add(k)
        out.append(p)
    return out


def closed_shadow_trades(db, sid: str, trade_date: str, cal: Sequence[str]) -> list[tuple[str, str, str, float]]:
    """返回 [(exit_date, entry_date, code, net%)]，只含 T 时点已确定的平仓。"""
    from collections import defaultdict
    from sqlalchemy import select
    from app.engine.backtest import BarInput
    from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY as EP
    from app.models.market import DailyKbar
    import app.services.backtest.policy_experiment as PE

    ti = cal.index(trade_date)
    start = cal[max(0, ti - LOOKBACK_TD)]
    picks = shadow_picks(db, sid, trade_date, start)
    if not picks:
        return []
    lim = EP.entry_lookahead_days + EP.horizon_days + 2
    codes = sorted({p["code"] for p in picks})
    rows = db.execute(
        select(DailyKbar.code, DailyKbar.trade_date, DailyKbar.open, DailyKbar.high, DailyKbar.low, DailyKbar.close, DailyKbar.volume)
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date > start, DailyKbar.trade_date <= trade_date)
        .order_by(DailyKbar.code, DailyKbar.trade_date)
    ).all()
    bars: dict[str, list] = defaultdict(list)
    for r in rows:
        pr = tuple(float(x or 0) for x in (r.open, r.high, r.low, r.close))
        if min(pr) <= 0:
            continue
        bars[str(r.code)].append(BarInput(code=str(r.code), trade_date=str(r.trade_date), open=pr[0], high=pr[1], low=pr[2],
                                          close=pr[3], volume=float(r.volume) if r.volume is not None else None,
                                          tradable=bool(float(r.volume or 0) > 0)))

    class _Day:
        pass

    out = []
    for p in picks:
        sd = p["signal_date"]
        end = (date.fromisoformat(sd) + timedelta(days=42)).isoformat()
        b = tuple(x for x in bars.get(p["code"], ()) if sd < str(x.trade_date) <= end)[:lim]
        if not b:
            continue
        day = _Day(); day.signal_date = sd; day.outcome_bars = {p["code"]: b}
        card = {"code": p["code"], "recommend_price": p["recommend_price"], "entry_mode": p.get("entry_mode") or "target_touch",
                "exit_strategy_id": sid}
        try:
            tr = (PE._simulate_card(day, card, EP) or {}).get("trade")
        except Exception:  # noqa: BLE001
            logger.warning("cooldown shadow simulate failed %s %s", sd, p["code"], exc_info=True)
            continue
        if not tr or not tr.executed or not tr.exit_date:
            continue
        ex = str(tr.exit_date)[:10]
        last = str(b[-1].trade_date)[:10]
        provisional = len(b) < lim and ex == last and str(tr.exit_reason or "").startswith(("horizon", "limit_down_locked_horizon"))
        if ex > trade_date or provisional:
            continue
        out.append((ex, str(tr.entry_date)[:10], p["code"], float(tr.net_return_pct)))
    out.sort()
    return out


def _calendar(db, trade_date: str) -> list[str]:
    from sqlalchemy import select
    from app.models.market import DailyKbar

    lo = (date.fromisoformat(trade_date) - timedelta(days=LOOKBACK_TD * 2 + 30)).isoformat()
    cal = sorted({str(r[0])[:10] for r in db.execute(
        select(DailyKbar.trade_date).where(DailyKbar.code == "000001", DailyKbar.trade_date >= lo,
                                           DailyKbar.trade_date <= trade_date))})
    if trade_date not in cal:
        cal.append(trade_date)
    return cal


def cooldown_state(db, sid: str, trade_date: str) -> dict[str, Any] | None:
    rule = COOLDOWN_RULES.get(sid)
    if not rule:
        return None
    cal = _calendar(db, trade_date)
    closed = closed_shadow_trades(db, sid, trade_date, cal)
    st = equity_ma_blocked([(c[0], c[3]) for c in closed], cal, trade_date)
    st.update({"rule": rule, "strategy_id": sid, "trade_date": trade_date})
    return st


def _is_live_formal(db, run_id: str) -> bool:
    from app.models.recommendation import RecommendationRun

    run = db.get(RecommendationRun, run_id)
    return bool(run is not None and run.evaluation_scope == "live_forward" and run.run_mode == "formal")


def apply_live_cooldown(db, run_id: str, sid: str | None, trade_date: str,
                        formal_cards: Sequence[Mapping[str, Any]]) -> tuple[list, dict | None]:
    """单策略 live formal run：冷却中 → 选股记为影子单（写盘）后清空正式推荐。其余情况原样返回。"""
    cards = list(formal_cards)
    if not sid or sid not in COOLDOWN_RULES or not cards or not trade_date or trade_date < LIVE_FROM:
        return cards, None
    try:
        if not _is_live_formal(db, run_id):
            return cards, None
        st = cooldown_state(db, sid, trade_date)
    except Exception:  # noqa: BLE001 - 冷却是附加风控，出错放行
        logger.warning("shortline cooldown check failed for %s %s; not blocking", sid, trade_date, exc_info=True)
        return cards, None
    if not st or not st.get("active"):
        return cards, st
    picks = [{"signal_date": trade_date, "code": str(c.get("code")), "name": c.get("name"),
              "recommend_price": float(c.get("recommend_price") or c.get("price") or 0),
              "entry_mode": c.get("entry_mode") or (c.get("feature_snapshot") or {}).get("entry_mode") or "target_touch",
              "final_score": c.get("final_score")} for c in cards]
    try:
        d = SHADOW_DIR / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{trade_date}.json").write_text(json.dumps({"state": st, "picks": picks}, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("cooldown shadow write failed for %s %s", sid, trade_date, exc_info=True)
    st = dict(st, blocked=[p["code"] for p in picks])
    logger.info("shortline cooldown active: %s %s blocked %s", sid, trade_date, st["blocked"])
    return [], st


__all__ = ["COOLDOWN_RULES", "LIVE_FROM", "apply_live_cooldown", "closed_shadow_trades", "cooldown_state", "equity_ma_blocked"]
