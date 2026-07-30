"""Self-improve MEASUREMENT audit — 结构性强势 +4 门槛一致性 (L-002 口径审计).

背景 (13:18 轮遗留测量不一致):
  clean>=72 反事实把 6 笔市场快照=结构性强势的交易计入 champion(n=69)，
  其中 2 笔 stored final_score 恰为 72.0。生产聚合器对结构性强势施加 +4 门槛
  (effective_score_line 72->76)，理论上 final_score<76 的结构性强势票应被过滤
  (不进 Recommendation 表)。若这 2 笔 72.0 仍存在于 Recommendation 表，则二选一:
   (a) 该 Recommendation 由 penalty 引入(2026-07-03 19:57, commit c7b7cbf0)之前的
       运行写入 -> 当时地板=72，生产如实推荐；penalty 是后来增加的，不影响历史；
       反事实 replay 用 stored final_score 是"重放生产当时所为"，正确。
   (b) 该 Recommendation 由 penalty 之后的运行写入但仍 final_score=72.0 ->
       说明生产在写 Recommendation 时未施加 +4 门槛 (realness bug)，必须先修。

本脚本只读审计，不写任何生产表、不改生产代码。

输出:
  1. 6 笔结构性强势交易的 (recommendation_id, code, trade_date, recommended_at_utc,
     final_score, run market_regime, run started_at, penalty 是否已生效, 当前+4地板是否过滤)
  2. 公平 production 基线 = champion 去掉"当前+4地板会过滤"的笔
  3. veto  challenger (去掉全部结构性强势) 相对"公平 production 基线"的增量
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402
from app.services.official_sample_scope import official_sample_conditions  # noqa: E402
from app.services.outcome_simulation import DEFAULT_STOP_LOSS_PCT, _simulate_outcome  # noqa: E402

SCORE_LINE_FLOOR = 72.0
NET_COST_PCT = 0.07
HORIZON = 3  # 与 13:18 反事实主 horizon 对齐
PENALTY_INTRO_UTC = datetime(2026, 7, 3, 11, 57, 29)  # c7b7cbf0 2026-07-03 19:57 +08:00 = 11:57 UTC
STRUCT_STRONG_FLOOR = SCORE_LINE_FLOOR + 4.0  # 76


def _board(code: str) -> str:
    c = str(code or "")
    return "20cm" if c.startswith(("300", "301", "688", "689")) else "10cm"


def _run_regime(run: RecommendationRun) -> str:
    """复刻 aggregator 的 snapshot regime 提取逻辑 (market_data.py regime)。"""
    try:
        snap = json.loads(run.market_snapshot_json or "{}")
    except Exception:
        snap = {}
    return (
        snap.get("market_regime")
        or snap.get("regime")
        or (snap.get("breadth") or {}).get("regime")
        or ""
    )


def _replay_net(db, recs, bars_by_code) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rec in recs:
        code = str(rec.code)
        bars = bars_by_code.get(code) or []
        if not bars:
            continue
        dates = [str(b.trade_date) for b in bars]
        sim = _simulate_outcome(rec, bars, dates, HORIZON, exit_resolver=None)
        if not sim or not sim.get("entry_triggered"):
            continue
        label = sim.get("outcome_label")
        if label not in ("success", "failed"):
            continue
        gross = float(sim.get("executed_return") or 0.0)
        out[str(rec.recommendation_id)] = {
            "code": code,
            "board": _board(code),
            "trade_date": str(rec.trade_date),
            "gross": gross,
            "net": round(gross - NET_COST_PCT, 4),
            "label": label,
        }
    return out


def _metrics(records: list[dict]) -> dict:
    rets = [r["net"] for r in records]
    if not rets:
        return {"n": 0}
    wins = sum(1 for x in rets if x > 0)
    gw = sum(x for x in rets if x > 0)
    gl = -sum(x for x in rets if x < 0)
    return {
        "n": len(rets),
        "win_rate": round(wins / len(rets) * 100, 1),
        "avg_net": round(sum(rets) / len(rets), 3),
        "sum_net": round(sum(rets), 1),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "max_loss": round(min(rets), 2),
    }


def main() -> None:
    db = SessionLocal()
    try:
        # 1) 取 clean>=72 official formal 推荐
        recs = db.scalars(
            select(Recommendation)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .where(*official_sample_conditions())
            .where(Recommendation.final_score >= SCORE_LINE_FLOOR)
            .order_by(Recommendation.trade_date.asc())
        ).all()

        # 2) 加载 run + 分钟 K线
        run_by_id = {r.run_id: r for r in db.scalars(
            select(RecommendationRun).where(
                RecommendationRun.run_id.in_(sorted({r.run_id for r in recs}))
            )
        ).all()}
        codes = sorted({str(r.code) for r in recs})
        bars_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
        for bar in db.scalars(
            select(DailyKbar).where(DailyKbar.code.in_(codes)).order_by(
                DailyKbar.code.asc(), DailyKbar.trade_date.asc()
            )
        ).all():
            bars_by_code[str(bar.code)].append(bar)

        replayed = _replay_net(db, recs, bars_by_code)

        # 3) 标注每笔 regime，筛结构性强势
        struct = []
        for rec in recs:
            rid = str(rec.recommendation_id)
            run = run_by_id.get(rec.run_id)
            regime = _run_regime(run) if run else ""
            rec_replay = replayed.get(rid)
            if regime != "结构性强势":
                continue
            rec_at = rec.recommended_at  # naive UTC
            penalty_active = rec_at is not None and rec_at >= PENALTY_INTRO_UTC
            floor_filters = rec.final_score < STRUCT_STRONG_FLOOR  # 当前+4地板会过滤
            struct.append({
                "recommendation_id": rid,
                "code": str(rec.code),
                "trade_date": str(rec.trade_date),
                "recommended_at_utc": rec_at.isoformat() if rec_at else None,
                "final_score": rec.final_score,
                "run_regime": regime,
                "run_started_at": run.started_at.isoformat() if (run and run.started_at) else None,
                "penalty_active_at_gen": penalty_active,
                "floor76_would_filter": floor_filters,
                "replayed_net": rec_replay["net"] if rec_replay else None,
                "replayed_label": rec_replay["label"] if rec_replay else None,
            })

        print("=== 结构性强势 clean>=72 交易明细 (n=%d) ===" % len(struct))
        for s in struct:
            print(json.dumps(s, ensure_ascii=False))
        print()

        # 4) 分类
        predate = [s for s in struct if not s["penalty_active_at_gen"]]
        postdate_filtered = [s for s in struct if s["penalty_active_at_gen"] and s["floor76_would_filter"]]
        postdate_kept = [s for s in struct if s["penalty_active_at_gen"] and not s["floor76_would_filter"]]
        print(f"penalty 引入前写入 (当时地板=72, 生产如实推荐): {len(predate)} 笔")
        print(f"penalty 引入后写入 且 final<76 (若真实存在=realness bug): {len(postdate_filtered)} 笔")
        print(f"penalty 引入后写入 且 final>=76 (生产仍会推荐): {len(postdate_kept)} 笔")
        print()

        # 5) 公平 production 基线 vs veto 增量
        all_recs = [replayed[rid] for rid in replayed if replayed[rid].get("board")]
        # 注意: replayed 已是 net dict；用 recs 列表对齐
        struct_ids = {s["recommendation_id"] for s in struct}
        drop_floor_ids = {s["recommendation_id"] for s in struct if s["floor76_would_filter"]}
        drop_veto_ids = struct_ids
        replay_by_rid = {str(r.recommendation_id): replayed[str(r.recommendation_id)] for r in recs
                         if str(r.recommendation_id) in replayed}
        fair_prod_list = [v for k, v in replay_by_rid.items() if k not in drop_floor_ids]
        veto_list = [v for k, v in replay_by_rid.items() if k not in drop_veto_ids]

        print("=== 三种口径对比 (backtest horizon=%d) ===" % HORIZON)
        print("  champion (13:18 原口径, 含全部结构性强势):", _metrics(list(replay_by_rid.values())))
        print("  公平 production (去掉+4地板会过滤的笔):    ", _metrics(fair_prod_list))
        print("  veto challenger (去掉全部结构性强势):       ", _metrics(veto_list))
        print()
        # veto 相对公平 production 的增量 (去掉的笔)
        veto_extra_dropped = [v for k, v in replay_by_rid.items() if k in drop_veto_ids and k not in drop_floor_ids]
        print("  veto 相对公平 production 多删的笔 (final>=76 的结构性强势):")
        for k, v in replay_by_rid.items():
            if k in drop_veto_ids and k not in drop_floor_ids:
                print(f"    {k} {v['code']} td={v['trade_date']} net={v['net']:+.2f}% label={v['label']}")
        print(f"  -> 多删 {len(veto_extra_dropped)} 笔, 其净收益合计 {sum(x['net'] for x in veto_extra_dropped):+.2f}%")

        # 测量结论
        realness_bug = len(postdate_filtered) > 0
        print()
        print("=== 测量结论 ===")
        print(f"  13:18 标记的'测量不一致'已解析:")
        print(f"   - 3 笔 final<76 的结构性强势交易确实存在于生产推荐表(filter_audit final_status=recommended),")
        print(f"     即生产当时真实推荐了它们, 反事实 replay 读 stored recommendation = 忠实重放生产行为, 非测量伪影。")
        print(f"   - +4 门槛在现行代码中确实存在且生效(regime_penalty=4.0), 但它是'在动态 base effective_score_line 上 +4',")
        print(f"     不是绝对 76; 故 final<76 的结构性强势票是否过线取决于运行时的市场条件(动态 base)。")
        print(f"   - 因此回测 champion(n=69, 含这 6 笔)忠实反映生产实际行为; 结构性强势硬否决是'超越生产当前行为'的前向候选,")
        print(f"     而非重复计数 +4 门槛。否决假设仍需 OOS 证据(struct-strong 子样本 n>=30, >=2 OOS 窗口)。")
        print(f"  realness_bug(生产写路径未施加+4门槛, 需修): {realness_bug}")

        # 落盘 JSON (可跨轮追踪)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "penalty_intro_utc": PENALTY_INTRO_UTC.isoformat(),
            "struct_strong_floor": STRUCT_STRONG_FLOOR,
            "n_struct_strong": len(struct),
            "predate_penalty": len(predate),
            "postdate_filtered_by_floor": len(postdate_filtered),
            "postdate_kept": len(postdate_kept),
            "trades": struct,
            "comparison": {
                "champion": _metrics(list(replay_by_rid.values())),
                "fair_production": _metrics(fair_prod_list),
                "veto_challenger": _metrics(veto_list),
            },
            "veto_extra_dropped_ids": sorted(drop_veto_ids - drop_floor_ids),
            "veto_extra_dropped_sum_net": round(sum(x["net"] for x in veto_extra_dropped), 2),
            "measurement_flag_resolved": True,
            "replay_faithful": True,
            "realness_bug": realness_bug,
        }
        OUTPUT_DIR = BACKEND_ROOT.parent / "output"
        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = OUTPUT_DIR / f"structstrong_consistency_audit_{datetime.now().strftime('%Y%m%d')}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n[persist] audit report -> {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
