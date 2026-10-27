#!/usr/bin/env python3
"""删除现有推荐记录并用当前上线策略回放重建（短线 + 中线 + 长线）。

默认 --dry-run：只读盘点 + 计划 + 中长线在临时目录试跑，**不删、不写生产表/生产 picks 目录**。
真正执行需同时给 --execute --confirm DELETE-AND-REBUILD，且不得在 16:35–18:35 运行。

口径：
- 短线：委托 scripts/replay_all_days.py（正式管线 + local_pool + trade_date_override 截断到当日，
  evaluation_scope=historical_replay / evidence_class=historical_reconstruction，结算走
  outcome_simulation_v2+r20261019），与 live_forward 天然区分，无需改表。
- 中线/长线：调用生产 midterm_service.generate_picks（同一套选股代码），OUT_DIR 临时重定向到
  staging 目录逐日顺序生成（持仓去重 _held_codes 与实盘一致），每个批次 JSON 加
  "origin": "replay"；收益由 _realize 按日K实时计算（与实盘相同）。无 origin 字段 = live。

用法：
  python scripts/rebuild_replay_picks.py --dry-run --date-range 2026-10-12:2026-10-16
  python scripts/rebuild_replay_picks.py --dry-run --strategies mid-repurchase-plan,long-repurchase-big
  python scripts/rebuild_replay_picks.py --execute --confirm DELETE-AND-REBUILD [--keep-live-forward]
  python scripts/rebuild_replay_picks.py --onboard <strategy_id>   # 新策略上线回填 3 个月（需开关）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text  # noqa: E402

REPLAY_ORIGIN = "replay"
CONFIRM_TOKEN = "DELETE-AND-REBUILD"
ONBOARD_ENV = "REPLAY_AUTO_BACKFILL_ENABLED"  # 默认关闭；全量重建完成后再设为 1
ONBOARD_MONTHS = 3
SHORT_DEFAULT_START = "2025-10-20"
MIDLONG_DEFAULT_START = "2025-10-20"
SHORT_SEC_PER_DAY = 45  # 2026-08/09 历史重建 302 个 run 实测均值 43s

# 删除顺序：子表在前。仅动推荐/结算相关表；llm_review_cache、*_bak_* 不动。
SHORT_DELETE_ORDER = [
    ("intraday_lock_signals", "monitor_id IN (SELECT monitor_id FROM intraday_lock_monitors WHERE recommendation_id IN ({rec}))"),
    ("intraday_lock_monitors", "recommendation_id IN ({rec})"),
    ("agent_decision_logs", "run_id IN ({run})"),
    ("recommendation_outcome_versions", "recommendation_id IN ({rec})"),
    ("recommendation_outcomes", "recommendation_id IN ({rec})"),
    ("recommendation_execution_states", "recommendation_id IN ({rec})"),
    ("recommendation_collection_seals", "run_id IN ({run})"),
    ("recommendation_evidence_classification_audits", "run_id IN ({run})"),
    ("recommendations", "run_id IN ({run})"),
    ("recommendation_runs", "run_id IN ({run})"),
]


def _run_filter(keep_live: bool) -> str:
    base = "SELECT run_id FROM recommendation_runs"
    return base + (" WHERE evaluation_scope <> 'live_forward'" if keep_live else "")


def _rec_filter(keep_live: bool) -> str:
    return f"SELECT recommendation_id FROM recommendations WHERE run_id IN ({_run_filter(keep_live)})"


def _trade_dates(db, start: str, end: str) -> list[str]:
    rows = db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' "
                           "AND trade_date BETWEEN :s AND :e ORDER BY trade_date"), {"s": start, "e": end})
    return [str(r[0]) for r in rows]


def _latest_trade_date(db) -> str:
    return str(db.execute(text("SELECT MAX(trade_date) FROM daily_kbars WHERE code='000001'")).scalar())


# ---------------------------------------------------------------- inventory
def inventory(db, picks_dir: Path) -> dict:
    q = lambda s: [tuple(str(x) for x in r) for r in db.execute(text(s))]  # noqa: E731
    inv: dict = {"tables": {}}
    for t, _ in SHORT_DELETE_ORDER + [("recommendation_reconstruction_batches", "")]:
        inv["tables"][t] = int(db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar())
    inv["runs_by_scope"] = q("SELECT evaluation_scope, COALESCE(evidence_class,'-'), COUNT(*), MIN(trade_date), "
                             "MAX(trade_date) FROM recommendation_runs GROUP BY 1,2 ORDER BY 1,2")
    inv["recs_by_scope"] = q("SELECT r.evaluation_scope, COUNT(*), MIN(x.trade_date), MAX(x.trade_date) "
                             "FROM recommendations x JOIN recommendation_runs r USING(run_id) GROUP BY 1")
    inv["outcomes_by_scope_rule"] = q(
        "SELECT r.evaluation_scope, o.rule_version, COALESCE(o.outcome_label,'-'), COUNT(*) FROM recommendation_outcomes o "
        "JOIN recommendations x USING(recommendation_id) JOIN recommendation_runs r USING(run_id) GROUP BY 1,2,3")
    inv["recs_by_setup_month"] = q(
        "SELECT r.evaluation_scope, DATE_FORMAT(x.trade_date,'%Y-%m'), COALESCE(x.setup_type,'-'), COUNT(*) "
        "FROM recommendations x JOIN recommendation_runs r USING(run_id) GROUP BY 1,2,3 ORDER BY 1,2,3")
    mid: dict = {}
    for f in sorted(picks_dir.glob("*.json")) if picks_dir.exists() else []:
        d = json.loads(f.read_text())
        k = f"{d['strategy_id']}|{d.get('origin', 'live')}"
        m = mid.setdefault(k, {"batches": 0, "picks": 0, "first": d["signal_date"], "last": d["signal_date"]})
        m["batches"] += 1
        m["picks"] += len(d.get("picks", []))
        m["first"] = min(m["first"], d["signal_date"])
        m["last"] = max(m["last"], d["signal_date"])
    inv["midlong_picks"] = mid
    return inv


def short_plan(db, start: str, end: str) -> dict:
    dates = _trade_dates(db, start, end)
    aset = {str(r[0]) for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM auction_data WHERE trade_date BETWEEN :s AND :e"), {"s": start, "e": end})}
    miss = [d for d in dates if d not in aset]
    return {"start": start, "end": end, "trade_days": len(dates), "auction_missing_days": len(miss),
            "auction_missing_sample": miss[:15],
            "est_minutes": round(len(dates) * SHORT_SEC_PER_DAY / 60)}


# ---------------------------------------------------------------- mid/long replay
MID_RETRY_WAITS = (120, 300, 600, 900)  # 同一天 no_data/error 的策略按序重试（历史日 no_data = 取数失败）


def replay_midlong(db, start: str, end: str, strategies: list[str] | None, staging: Path,
                   stop_at: dt.time | None = None, *, apply_daily_cap: bool = False,
                   retry_waits: tuple[int, ...] | None = None) -> dict:
    """在 staging 目录里逐交易日顺序跑生产 generate_picks；返回统计。不碰生产 OUT_DIR。

    可续跑：staging 旁的 progress.json 记录已完成到哪一天，同一 staging 再跑从下一天接着做
    （_held_codes 只看 staging 里 as_of 之前的批次，顺序不被打乱）。某天有策略 no_data/error 时
    按 MID_RETRY_WAITS 只重试这些策略，仍失败才记入 stats。到 stop_at 在日边界停下，stats["stopped"]=True。"""
    from app.services import midterm_service as ms

    staging.mkdir(parents=True, exist_ok=True)
    prog_f = staging.parent / "progress.json"
    prog = json.loads(prog_f.read_text()) if prog_f.exists() else {}
    stats = {"days": 0, "errors": prog.get("errors", {}), "no_data": prog.get("no_data", {}),
             "seconds": 0.0, "stopped": False}
    from app.services import midterm_forecast as mf

    orig_out, orig_store = ms.OUT_DIR, ms._store_repurchase_events
    orig_fc_fetch, orig_fc_store = mf._fetch_forecast_day, mf._store_live
    ms.OUT_DIR = staging
    ms._store_repurchase_events = lambda new: None  # 回放不改研究事件库
    # 历史日的业绩预告改读本地逐股全历史（backfill_longterm），不再按公告日逐天打 promax：
    # 2026-10-19 23:30 起 promax forecast 持续 upstream_pool_exhausted；本地与 API 对 20251018~24 逐行一致。
    mf._fetch_forecast_day = _local_forecast_reader(orig_fc_fetch)
    mf._store_live = lambda new: None
    t0 = time.time()
    try:
        for d in _trade_dates(db, start, end):
            if d <= prog.get("done_through", ""):
                continue
            if stop_at and dt.datetime.now().time() >= stop_at and dt.datetime.now().time() < dt.time(16, 0):
                stats["stopped"] = True
                break
            pending, waits = strategies, list(MID_RETRY_WAITS if retry_waits is None else retry_waits)
            while True:
                res = ms.generate_picks(db, d, strategy_ids=pending, apply_daily_cap=apply_daily_cap)
                bad = [sid for sid, raw in res.items()
                       if raw == "no_data" or (isinstance(raw, dict) and "error" in raw)]
                if not bad or not waits:
                    break
                w = waits.pop(0)
                print(f"[midlong] {d} retry {bad} in {w}s", flush=True)
                time.sleep(w)
                pending = bad
            for sid, raw in res.items():
                if isinstance(raw, dict) and "error" in raw:
                    stats["errors"].setdefault(sid, []).append(d)
                elif raw == "no_data":  # 多为数据源限流/缺数，必须为 0 才算干净重建
                    stats["no_data"].setdefault(sid, []).append(d)
            stats["days"] += 1
            prog = {"done_through": d, "errors": stats["errors"], "no_data": stats["no_data"]}
            prog_f.write_text(json.dumps(prog, ensure_ascii=False))
            print(f"[midlong] done {d} bad={bad}", flush=True)
    finally:
        ms.OUT_DIR, ms._store_repurchase_events = orig_out, orig_store
        mf._fetch_forecast_day, mf._store_live = orig_fc_fetch, orig_fc_store
        stats["seconds"] = round(time.time() - t0, 1)
    stats["done_through"] = prog.get("done_through")
    tag_dir(staging)
    by: dict = {}
    for f in staging.glob("*.json"):
        dd = json.loads(f.read_text())
        b = by.setdefault(dd["strategy_id"], {"batches": 0, "picks": 0})
        b["batches"] += 1
        b["picks"] += len(dd["picks"])
    stats["by_strategy"] = by
    return stats


def _local_forecast_reader(api_fetch):
    """按公告日从 data/midterm/forecast/<code>.parquet 取全市场预告；超出本地覆盖的日期回落 API。"""
    import pandas as pd
    from app.services import midterm_forecast as mf

    parts = [pd.read_parquet(f, columns=mf.COLS) for f in sorted(mf.FC_DIR.glob("*.parquet"))]
    frames = [p for p in parts if len(p)]
    if not frames:
        # CI 和空数据目录没有本地预告 parquet。pd.concat([]) 会抛错；没有覆盖就走 API。
        return api_fetch
    df = pd.concat(frames, ignore_index=True)
    df["ann_date"] = df["ann_date"].astype(str).str.strip()
    covered_through = df["ann_date"].max()
    groups = {k: g.reset_index(drop=True) for k, g in df.groupby("ann_date")}
    empty = df.iloc[0:0]

    def fetch(day: str):
        if day > covered_through:
            return api_fetch(day)
        return groups.get(day, empty)

    return fetch


def tag_dir(d: Path) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    for f in d.glob("*.json"):
        dd = json.loads(f.read_text())
        if dd.get("origin") != REPLAY_ORIGIN:
            dd["origin"] = REPLAY_ORIGIN
            dd["replayed_at"] = now
            f.write_text(json.dumps(dd, ensure_ascii=False, indent=1))


def summarize_realized(db, staging: Path) -> dict:
    """用生产 _load_batch（含 _realize）汇总已平仓收益：笔数/胜率/均值/PF。"""
    from app.services import midterm_service as ms
    out: dict = {}
    for f in staging.glob("*.json"):
        b = ms._load_batch(db, f)
        s = out.setdefault(b["strategy_id"], [])
        s += [p["ret"] for p in b["picks"] if p.get("status") == "closed" and p.get("ret") is not None]
    res = {}
    for sid, rets in out.items():
        win = [r for r in rets if r > 0]
        loss = -sum(r for r in rets if r < 0)
        res[sid] = {"closed": len(rets), "win_rate": round(len(win) / len(rets), 3) if rets else None,
                    "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
                    "pf": round(sum(win) / loss, 2) if loss else None}
    return res


# ---------------------------------------------------------------- onboarding hook
def onboard_enabled() -> bool:
    return os.environ.get(ONBOARD_ENV, "0") == "1"


def onboard_strategy(db, strategy_id: str, *, today: str | None = None, dry_run: bool = False) -> dict:
    """新中长线策略上线：回填最近 3 个月 replay 批次到生产 picks 目录（只写该策略，不覆盖已有文件）。

    短线新策略不在此处理：短线回放按整个正式 preset 跑，需要用 replay_all_days.py
    对最近 3 个月跑一次（见 docs/strategy_onboarding_replay.md）。"""
    from app.services import midterm_service as ms
    if not dry_run and not onboard_enabled():
        return {"skipped": f"{ONBOARD_ENV}!=1（全量重建完成前保持关闭）"}
    if strategy_id not in ms.STRATEGIES:
        raise SystemExit(f"unknown mid/long strategy {strategy_id}")
    end = today or _latest_trade_date(db)
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=ONBOARD_MONTHS * 31)).isoformat()
    staging = Path(tempfile.mkdtemp(prefix=f"onboard_{strategy_id}_")) / "picks"
    stats = replay_midlong(db, start, end, [strategy_id], staging)
    written = 0
    if not dry_run:
        ms.OUT_DIR.mkdir(parents=True, exist_ok=True)
        for f in staging.glob(f"*_{strategy_id}.json"):
            if not (ms.OUT_DIR / f.name).exists():
                shutil.copy2(f, ms.OUT_DIR / f.name)
                written += 1
    stats.update({"start": start, "end": end, "written": written, "staging": str(staging)})
    return stats


# ---------------------------------------------------------------- execute
def _in_blackout(now: dt.datetime) -> bool:
    return dt.time(16, 35) <= now.time() <= dt.time(18, 35)


def execute(db, args, short_rng, mid_rng, strategies) -> dict:
    from app.services import midterm_service as ms
    if _in_blackout(dt.datetime.now()):
        raise SystemExit("16:35–18:35 禁止运行（17:35 日更窗口）")
    keep = args.keep_live_forward
    report: dict = {"deleted": {}}
    # 1) 中长线先在 staging 生成，成功后再删再换，避免删了生成失败
    staging = (Path(args.staging) if args.staging
               else BASE_DIR / "output" / f"replay_staging_{dt.datetime.now():%Y%m%d%H%M}") / "picks"
    if not args.skip_midlong:
        stop_at = dt.time.fromisoformat(args.stop_at) if args.stop_at else None
        report["midlong"] = replay_midlong(db, mid_rng[0], mid_rng[1], strategies, staging, stop_at=stop_at)
        if report["midlong"]["stopped"]:
            raise SystemExit(f"中长线回放到 {args.stop_at} 暂停于 {report['midlong']['done_through']}，"
                             f"未删除任何记录；同一 --staging 再跑即续上")
        if (report["midlong"]["errors"] or report["midlong"]["no_data"]) and not args.allow_no_data:
            raise SystemExit("中长线回放存在 error/no_data 日（多为限流），未删除任何记录：" + json.dumps(
                {k: report["midlong"][k] for k in ("errors", "no_data")}, ensure_ascii=False))
    # 2) 删短线
    if not args.skip_short:
        rec, run = _rec_filter(keep), _run_filter(keep)
        for t, where in SHORT_DELETE_ORDER:
            sql = f"DELETE FROM {t} WHERE " + where.format(rec=f"SELECT * FROM ({rec}) a", run=f"SELECT * FROM ({run}) b")
            report["deleted"][t] = db.execute(text(sql)).rowcount
        if not keep:
            report["deleted"]["recommendation_reconstruction_batches"] = db.execute(
                text("DELETE FROM recommendation_reconstruction_batches")).rowcount
        db.commit()
    # 3) 换中长线 picks
    if not args.skip_midlong:
        n = 0
        for f in list(ms.OUT_DIR.glob("*.json")):
            d = json.loads(f.read_text())
            if strategies and d["strategy_id"] not in strategies:
                continue
            if keep and d.get("origin") != REPLAY_ORIGIN:
                continue
            f.unlink()
            n += 1
        report["deleted"]["midlong_pick_files"] = n
        ms.OUT_DIR.mkdir(parents=True, exist_ok=True)
        for f in staging.glob("*.json"):
            if keep and (ms.OUT_DIR / f.name).exists():
                continue  # 同日 live 批次优先
            shutil.copy2(f, ms.OUT_DIR / f.name)
        shutil.rmtree(staging.parent, ignore_errors=True)
    # 4) 短线回放（正式管线，需 Prefect worker 在线）
    if not args.skip_short:
        # 与 live 一致：每个现役短线策略各自一个单策略 run（10-14 起），逐策略一个重建批次。
        from app.services.standalone_strategies import standalone_strategy_ids
        from app.services.workbench_service import sync_skills
        sids = standalone_strategy_ids(sync_skills(db))
        report["short_strategies"] = sids
        report["short_replay_exit"] = {}
        for sid in sids:
            if _in_blackout(dt.datetime.now()):
                report["short_replay_exit"][sid] = "blackout_skipped"
                continue
            cmd = [sys.executable, str(BASE_DIR / "scripts" / "replay_all_days.py"),
                   "--start", short_rng[0], "--end", short_rng[1], "--single-skill", sid,
                   "--attempt-tag", f"rebuild-{sid}", "--skip-existing"]
            if args.rule_only_fallback:
                cmd.append("--rule-only-fallback")
            report["short_replay_exit"][sid] = subprocess.call(cmd, cwd=str(BASE_DIR))
    return report


def _rng(raw: str | None, default_start: str, latest: str) -> tuple[str, str]:
    if raw:
        a, _, b = raw.partition(":")
        return a, (b or latest)
    return default_start, latest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", default="")
    ap.add_argument("--date-range", help="同时作用于短线和中长线，START:END")
    ap.add_argument("--short-range")
    ap.add_argument("--midlong-range")
    ap.add_argument("--strategies", help="中长线策略 id 逗号分隔；短线按整个正式 preset 回放")
    ap.add_argument("--keep-live-forward", action="store_true", help="保留真实 live_forward 记录")
    ap.add_argument("--skip-short", action="store_true")
    ap.add_argument("--skip-midlong", action="store_true")
    ap.add_argument("--rule-only-fallback", action="store_true")
    ap.add_argument("--allow-no-data", action="store_true", help="中长线有 no_data 日也继续（不推荐）")
    ap.add_argument("--onboard", metavar="STRATEGY_ID")
    ap.add_argument("--json-out")
    ap.add_argument("--staging", help="中长线 staging 目录（固定路径可续跑，见 progress.json）")
    ap.add_argument("--stop-at", help="HH:MM，中长线回放到点在日边界暂停（不删不换），再跑续上")
    args = ap.parse_args()

    from app.db.session import SessionLocal
    db = SessionLocal()
    latest = _latest_trade_date(db)
    strategies = [s for s in (args.strategies or "").split(",") if s] or None
    if args.onboard:
        out = onboard_strategy(db, args.onboard, dry_run=not args.execute)
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        return 0
    short_rng = _rng(args.short_range or args.date_range, SHORT_DEFAULT_START, latest)
    mid_rng = _rng(args.midlong_range or args.date_range, MIDLONG_DEFAULT_START, latest)
    from app.services import midterm_service as ms
    report: dict = {"db": db.execute(text("SELECT DATABASE()")).scalar(), "latest_trade_date": latest,
                    "inventory": inventory(db, ms.OUT_DIR), "short_plan": short_plan(db, *short_rng),
                    "midlong_range": mid_rng}
    if args.execute:
        if args.confirm != CONFIRM_TOKEN:
            raise SystemExit(f"--execute 需要 --confirm {CONFIRM_TOKEN}")
        report["execute"] = execute(db, args, short_rng, mid_rng, strategies)
    else:
        staging = Path(tempfile.mkdtemp(prefix="replay_dryrun_")) / "picks"
        if not args.skip_midlong:
            report["midlong_dry_run"] = replay_midlong(db, mid_rng[0], mid_rng[1], strategies, staging)
            report["midlong_dry_run"]["realized"] = summarize_realized(db, staging)
            report["midlong_dry_run"]["staging"] = str(staging)
        db.rollback()
    txt = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if args.json_out:
        Path(args.json_out).write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
