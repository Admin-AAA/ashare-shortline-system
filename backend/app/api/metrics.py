from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import logging

from app.db.session import get_db
from app.services.outcome_service import (
    board_and_net_breakdown,
    filter_effectiveness,
    metrics_by_score_line,
    metrics_by_setup,
    metrics_by_strategy,
    overview_metrics,
    precision_at_k,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# 时间窗口通用参数（每个端点必须用独立 Query() 实例，FastAPI 不允许复用同一对象）。
# - window_days: 滚动窗口天数（如 30/90）。设置后自动 start=today-N, end=today-SETTLE_LAG，
#   保证窗口内推荐均已走完 horizon 结算，避免 pending 污染样本。
# - start_date / end_date: 显式 ISO 日期（YYYY-MM-DD），优先级低于 window_days。
#   不传则统计全部历史累计。
def _window_days_q() -> "Query":
    return Query(default=None, ge=1, le=365, description="滚动窗口天数，如 30/90；设置后自动截到已结算截止日")


def _start_date_q() -> "Query":
    return Query(default=None, description="显式起始日期 YYYY-MM-DD（window_days 优先）")


def _end_date_q() -> "Query":
    return Query(default=None, description="显式截止日期 YYYY-MM-DD（window_days 优先）")


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return overview_metrics(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/d-net")
def d_net(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    """D 项净成本 + 板块(20cm/10cm)分解（北极星 D 项 observability）。"""
    return board_and_net_breakdown(
        db, window_days=window_days, start_date=start_date, end_date=end_date,
    )


@router.get("/by-strategy")
def by_strategy(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return metrics_by_strategy(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/by-setup")
def by_setup(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return metrics_by_setup(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/precision-at-k")
def metric_precision_at_k(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return precision_at_k(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/by-score-line")
def by_score_line(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return metrics_by_score_line(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/filter-effectiveness")
def metric_filter_effectiveness(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return filter_effectiveness(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/d-diagnostics")
def d_diagnostics(db: Session = Depends(get_db)):
    """D 项诊断摘要：真残留毒区 + 伪活跃反事实（与 self_check / verify_d_guardrail 同源）。

    供指标页「系统可信度」一屏可见；不改主口径胜率，仅诊断。
    不重标历史 formal。
    """
    import sys
    from pathlib import Path

    from sqlalchemy import text

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import self_check  # type: ignore[import-not-found]

    skills = self_check._load_skills_once()
    if not skills:
        return {
            "residual_n": 0,
            "modes": [],
            "pseudo_active": None,
            "triage": None,
            "phase_information": None,
            "note": "no skills loaded",
        }

    cov_rows = db.execute(
        text(
            """
            SELECT r.recommendation_id, o.outcome_label, o.executed_return,
                   r.feature_snapshot_json, r.skill_breakdown_json,
                   o.exit_reason, o.max_return, r.trade_date, r.final_score
            FROM recommendation_outcomes o
            JOIN recommendations r ON r.recommendation_id = o.recommendation_id
            JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
            WHERE o.outcome_label IN ('success', 'failed')
              AND r.recommendation_id LIKE 'rec_%'
            """
        )
    ).fetchall()
    uncovered = self_check._collect_uncovered_failures(cov_rows, skills)

    # 模式分布（与 self_check 毒区一致）
    from collections import Counter

    mode_c: Counter = Counter()
    mode_ret: dict[str, list[float]] = {}
    for u in uncovered:
        fs = u.get("fs") or {}
        mode = self_check._classify_failure_mode_inline(
            exit_reason=u.get("exit_reason"),
            executed_return=u.get("executed_return"),
            max_return=u.get("max_return"),
            rsi_12=fs.get("rsi_12"),
            chg20=fs.get("chg20"),
            pos60=fs.get("pos60"),
            setup_type=fs.get("setup_type"),
        )
        mode_c[mode] += 1
        if u.get("executed_return") is not None:
            mode_ret.setdefault(mode, []).append(float(u["executed_return"]))
    modes = [
        {
            "mode": m,
            "n": c,
            "avg_return": round(sum(mode_ret.get(m, [0])) / max(1, len(mode_ret.get(m, []))), 2)
            if mode_ret.get(m)
            else 0.0,
        }
        for m, c in mode_c.most_common()
    ]

    snap_rows = db.execute(
        text(
            """
            SELECT trade_date, sample_count, up_count, limit_up_count, limit_down_count
            FROM market_snapshots
            """
        )
    ).fetchall()
    market_snaps = {
        str(r[0]): {
            "sample_count": r[1],
            "up_count": r[2],
            "limit_up_count": r[3],
            "limit_down_count": r[4],
        }
        for r in snap_rows
    }
    pa = (
        self_check._pseudo_active_sentiment_counterfactual(uncovered, market_snaps)
        if uncovered
        else None
    )
    pseudo = None
    if pa:
        pseudo = {
            "weak_active_n": pa["weak_active_n"],
            "pseudo_n": pa["pseudo_n"],
            "still_active_n": pa["still_active_n"],
            "days": [
                {
                    "trade_date": td,
                    "n": d["n"],
                    "pseudo": d["pseudo"],
                    "new_sent": d.get("new_sent"),
                    "lu": d.get("lu"),
                    "ld": d.get("ld"),
                }
                for td, d in sorted((pa.get("day_breakdown") or {}).items())
            ],
            "note": (
                "弱势·活跃主因历史伪活跃(涨停孤岛+普跌)；T28 已修情绪入口；"
                "勿对 residual 弱势·活跃加 entry"
                if pa["pseudo_n"] >= max(3, pa["weak_active_n"] // 2)
                else None
            ),
        }

    # T35：residual triage（污染/伪活跃/单日簇/稀疏）— 与 self_check 同源
    poll_raw = db.execute(
        text(
            """
            SELECT r.code, r.trade_date, o.outcome_label, o.executed_return, u.score_line,
                   JSON_EXTRACT(r.feature_snapshot_json,
                                '$.score_line_meta.dynamic_soft_floor_applied') AS soft,
                   JSON_UNQUOTE(JSON_EXTRACT(r.feature_snapshot_json,
                                '$.score_line_meta.market_base')) AS mbase
            FROM recommendation_outcomes o
            JOIN recommendations r ON r.recommendation_id = o.recommendation_id
            JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
            WHERE o.outcome_label IN ('success', 'failed')
              AND r.recommendation_id LIKE 'rec_%'
            """
        )
    ).fetchall()
    pure_keys = set((self_check._pure_pollution_keys(list(poll_raw)) or {}).get("union") or set())
    triage_raw = (
        self_check._triage_residual_poison(
            uncovered,
            pure_pollution_keys=pure_keys,
            pseudo_active_rids=set((pa or {}).get("pseudo_rids") or []),
        )
        if uncovered
        else None
    )
    triage = None
    if triage_raw and triage_raw.get("n", 0) > 0:
        c = triage_raw["counts"]
        sparse_items = self_check._sparse_residual_items(
            uncovered, triage_raw.get("sparse_rids") or [],
        )
        soft_allowed_items = self_check._sparse_residual_items(
            uncovered, triage_raw.get("soft_allowed_rids") or [],
        )
        sparse_days = sorted({str(it.get("trade_date") or "") for it in sparse_items if it.get("trade_date")})
        triage = {
            "n": triage_raw["n"],
            "pure_pollution": c.get("pure_pollution", 0),
            "pseudo_active": c.get("pseudo_active", 0),
            "soft_allowed": c.get("soft_allowed", 0),
            "day_clustered": c.get("day_clustered", 0),
            "sparse": c.get("sparse", 0),
            "clustered_days": triage_raw.get("clustered_days") or [],
            "sparse_n": c.get("sparse", 0),
            "sparse_items": sparse_items,
            "soft_allowed_items": soft_allowed_items,
            "sparse_same_day": (
                sparse_days[0] if len(sparse_days) == 1 and len(sparse_items) >= 2 else None
            ),
            "ban_n": triage_raw.get("ban_n"),
            "note": triage_raw.get("note"),
            "actionable": c.get("sparse", 0) > 0,
        }

    # phase 信息量（T5→T7：self_check 每轮可见后，指标页一屏同步）
    phase_info = None
    try:
        import importlib.util

        diag_path = scripts / "diagnose_phase_information.py"
        spec = importlib.util.spec_from_file_location("diagnose_phase_information", diag_path)
        if spec is not None and spec.loader is not None:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            records = mod._fetch_live_formal_ledger(db)
            if records and len(records) >= 30:
                rep = mod.build_phase_report(records)
                alpha = getattr(mod, "ALPHA", 0.05)
                notables = [
                    {
                        "dim": b.get("dim"),
                        "name": b.get("name"),
                        "n": b.get("n"),
                        "winrate": b.get("winrate"),
                        "net_plr": b.get("net_plr"),
                        "avg_ret": b.get("avg_ret"),
                    }
                    for b in (rep.get("notable_buckets") or [])
                    if b.get("winrate", 100) < (rep.get("overall") or {}).get("winrate", 0)
                ][:4]
                phase_info = {
                    "n": rep["overall"]["n"],
                    "overall_winrate": rep["overall"]["winrate"],
                    "regime_chi2": rep["regime_chi2"],
                    "regime_p": rep["regime_p"],
                    "regime_significant": rep["regime_p"] < alpha,
                    "sentiment_chi2": rep["sentiment_chi2"],
                    "sentiment_p": rep["sentiment_p"],
                    "sentiment_significant": rep["sentiment_p"] < alpha,
                    "notable_low_buckets": notables,
                    "note": (
                        "sentiment 无独立信息量：防守投 regime 真亏桶，勿对 sentiment 加规则"
                        if rep["sentiment_p"] >= alpha and rep["regime_p"] < alpha
                        else (
                            "regime 信息量消失：勿在 phase 上加精细 veto（过拟合噪声）"
                            if rep["regime_p"] >= alpha
                            else None
                        )
                    ),
                }
    except Exception as exc:
        logger.warning("phase information for d-diagnostics failed: %s", exc)
        phase_info = None

    return {
        "residual_n": len(uncovered),
        "modes": modes,
        "pseudo_active": pseudo,
        "triage": triage,
        "phase_information": phase_info,
        "forward_epoch": self_check.FORWARD_EPOCH_PINNED,
    }


@router.get("/capital-flow-factor")
def capital_flow_factor(db: Session = Depends(get_db)):
    """资金流因子预测力分析（T1 新因子研发，前端可见）。

    复用 scripts/analyze_capital_flow_factor.py + feature_signal_guard.point_biserial 逻辑，
    返回各资金流字段 vs outcome 的 r_pb / 分组胜率 / 最强因子的 feature_signal_guard 判定。
    只读分析，不改策略。D 路径穷尽时因子是「待前瞻验证候选」非已验证。
    """
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from feature_signal_guard import point_biserial, evaluate_feature_signal  # type: ignore[import-not-found]
    from app.services.capital_flow_factor_evidence import (
        CAPITAL_FLOW_FIELDS,
        fetch_dedup_live_formal_capital_flow,
        summarize_discovery_oos,
    )

    rows = fetch_dedup_live_formal_capital_flow(db)

    if len(rows) < 20:
        return {"sample_count": len(rows), "factors": [], "best_factor": None,
                "oos": summarize_discovery_oos(rows),
                "note": "样本不足 20，不可分析；OOS证据不足时不得晋级"}

    factors = []
    best = None
    for field, name in CAPITAL_FLOW_FIELDS:
        pairs = [(float(r[field] or 0), 1 if r["outcome_label"] == "success" else 0)
                 for r in rows if r[field] is not None and abs(float(r[field] or 0)) > 0.001]
        if len(pairs) < 20:
            factors.append({"field": field, "name": name, "n": len(pairs), "r_pb": None,
                            "wr_pos": None, "wr_neg": None, "delta_pp": None, "note": "样本不足"})
            continue
        x = [p[0] for p in pairs]
        y = [p[1] for p in pairs]
        rpb = point_biserial(x, y)
        pos = [yi for xi, yi in pairs if xi > 0]
        neg = [yi for xi, yi in pairs if xi <= 0]
        wr_pos = round(sum(pos) * 100 / len(pos), 1) if pos else 0
        wr_neg = round(sum(neg) * 100 / len(neg), 1) if neg else 0
        factors.append({"field": field, "name": name, "n": len(pairs), "r_pb": round(rpb, 4) if rpb is not None else None,
                        "wr_pos": wr_pos, "wr_neg": wr_neg, "delta_pp": round(wr_pos - wr_neg, 1),
                        "note": None})
        if rpb is not None and (best is None or rpb > best["r_pb"]):
            best = {"field": field, "name": name, "n": len(pairs), "r_pb": round(rpb, 4)}

    # 最强因子的 feature_signal_guard 判定
    best_factor = None
    if best:
        pos_dates = sorted([
            str(r["trade_date"]) for r in rows
            if r[best["field"]] is not None and float(r[best["field"]] or 0) > 0.001
            and r["outcome_label"] == "success"
        ])
        guard = evaluate_feature_signal(pos_dates)
        best_factor = {**best, "guard": guard}

    oos = summarize_discovery_oos(rows)
    return {"sample_count": len(rows), "factors": factors, "best_factor": best_factor,
            "oos": oos,
            "note": "按code+trade_date去重；因子是待前瞻验证候选，OOS证据不足时不得晋级"}
