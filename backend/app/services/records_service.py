"""记录中心服务 —— 计算推荐股票的 1/3/5 日 + 至今收益率。

收益计算用 pct_chg 链式累乘（除权安全）。Tushare 的 pct_chg 字段已做除权调整，
链式累乘不会因除权日价格跳变产生虚假亏损（见 memory: ex-rights-detection）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Recommendation, RecommendationRun, StockMatrixHistory

logger = logging.getLogger(__name__)

# 收益率口径：1日/3日/5日（推荐日 T+0 不算，T+1 起）
RETURN_HORIZONS = [1, 3, 5]


def _load_future_bars(db: Session, code: str, trade_date: str) -> list[DailyKbar]:
    """加载某股票从推荐日（含）之后的所有日K，按 trade_date 升序。"""
    return list(
        db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code == code, DailyKbar.trade_date >= trade_date)
            .order_by(DailyKbar.trade_date.asc())
        ).all()
    )


def _compute_returns(recommend_price: float, bars: list[DailyKbar], today_date: str) -> dict[str, Any]:
    """计算 1/3/5 日 + 至今收益率。

    bars: 从推荐日（含）开始的日K，升序。bars[0] 是推荐日。
    - 1日收益 = T+1 收盘 / 推荐价 - 1（用 pct_chg 链式累乘避免除权失真）
    - 至今收益 = 最近一日收盘 / 推荐价 - 1

    用 pct_chg 累乘：return_n = prod(pct_chg[1..n] / 100 + 1) - 1
    """
    result: dict[str, Any] = {
        "return_1d": None,
        "return_3d": None,
        "return_5d": None,
        "return_to_date": None,
        "latest_date": None,
        "latest_close": None,
        "available_bars": len(bars),
    }
    if not bars or not recommend_price or recommend_price <= 0:
        return result

    # bars[0] 是推荐日本K；T+1 起的后续 bar
    future_bars = bars[1:]
    if not future_bars:
        # 只有推荐日一根K，无后续数据
        result["latest_date"] = bars[0].trade_date
        result["latest_close"] = bars[0].close
        return result

    # 用 pct_chg 链式累乘计算 N 日收益（pct_chg 已除权调整）
    for n in RETURN_HORIZONS:
        if len(future_bars) >= n:
            cum = 1.0
            for i in range(n):
                pct = float(future_bars[i].pct_chg or 0) / 100.0
                cum *= (1.0 + pct)
            result[f"return_{n}d"] = round((cum - 1.0) * 100, 2)

    # 至今收益：用最后一根 bar 的 close / recommend_price - 1
    # 这里直接用收盘价比例（至今收益是绝对收益，不是日累乘；除权日 close 已除权调整）
    latest = future_bars[-1]
    # 但若除权日存在于区间，close 序列有跳变。为除权安全，也用 pct_chg 全链累乘。
    cum = 1.0
    for b in future_bars:
        pct = float(b.pct_chg or 0) / 100.0
        cum *= (1.0 + pct)
    result["return_to_date"] = round((cum - 1.0) * 100, 2)
    result["latest_date"] = latest.trade_date
    result["latest_close"] = float(latest.close)

    # 标记 today_date 是否等于 latest_date（用于前端判断"今天是否有数据"）
    result["as_of_date"] = latest.trade_date
    return result


def list_run_records(
    db: Session,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """推荐运行记录列表（轻量，不含推荐股票明细）。

    在此（而非共享的 list_runs）补充每个 run 的「聚合 1/3/5 日 + 至今收益率」，
    避免拖慢 bootstrap / 仪表盘（它们也调用 list_runs）。
    使用 30s 缓存（与 metrics 端点一致），因为 run 记录变更频率低但查询频繁。
    """
    from app.services.outcome_service import _cached
    from app.services.workbench_service import list_runs

    key = f"run_records:{limit}"
    return _cached(key, lambda: _attach_run_aggregate_returns(db, list_runs(db, limit=limit)))


def _empty_agg_returns() -> dict[str, Any]:
    return {"return_1d": None, "return_3d": None, "return_5d": None, "return_to_date": None}


def _aggregate_returns(rec_returns: list[dict[str, Any]]) -> dict[str, Any]:
    """对多个 rec 的 returns 取各口径均值（忽略 None）。"""
    out = _empty_agg_returns()
    for k in ("return_1d", "return_3d", "return_5d", "return_to_date"):
        vals = [rr.get(k) for rr in rec_returns if rr.get(k) is not None]
        if vals:
            out[k] = round(sum(vals) / len(vals), 2)
    return out


def _attach_run_aggregate_returns(db: Session, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量计算每个 run 的聚合收益率（推荐股票 1/3/5 日 + 至今均值），挂到 run['returns']。

    一次性按 code 批量加载 K 线（>= 最小推荐日），与各 run 的推荐做链式累乘，
    与 get_run_record_detail 同日径；仅在 records 列表接口触发，不影响 bootstrap。
    """
    if not runs:
        return runs
    run_ids = [r["run_id"] for r in runs]
    recs = db.scalars(
        select(Recommendation).where(Recommendation.run_id.in_(run_ids))
    ).all()
    if not recs:
        for r in runs:
            r["returns"] = _empty_agg_returns()
        return runs

    today = _latest_kbar_date(db) or ""
    codes = list({r.code for r in recs})
    min_date = min(r.trade_date for r in recs)

    # 只取 _compute_returns 需要的 4 列（code/trade_date/close/pct_chg），不加载 open/high/low/volume
    # 等（9275 行 × 全列 ORM 实例化是 _attach 慢的主因）。Row 对象支持 .code/.trade_date 属性访问。
    rows = db.execute(
        select(
            DailyKbar.code,
            DailyKbar.trade_date,
            DailyKbar.close,
            DailyKbar.pct_chg,
        )
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date >= min_date)
        .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
    ).all()
    by_code: dict[str, list] = {}
    for b in rows:
        by_code.setdefault(b.code, []).append(b)

    per_rec: dict[str, dict[str, Any]] = {}
    for rc in recs:
        all_bars = by_code.get(rc.code, [])
        r_bars = [b for b in all_bars if b.trade_date >= rc.trade_date]
        try:
            price = float(rc.recommend_price or 0)
        except (TypeError, ValueError):
            price = 0.0
        per_rec[rc.recommendation_id] = _compute_returns(price, r_bars, today)

    recs_by_run: dict[str, list[Recommendation]] = {}
    for rc in recs:
        recs_by_run.setdefault(rc.run_id, []).append(rc)

    for run in runs:
        run_recs = recs_by_run.get(run["run_id"], [])
        rec_returns = [per_rec[rc.recommendation_id] for rc in run_recs if rc.recommendation_id in per_rec]
        run["returns"] = _aggregate_returns(rec_returns)
    return runs


def get_run_record_detail(db: Session, run_id: str, today_date: str | None = None) -> dict[str, Any] | None:
    """单次推荐运行详情 + 当次推荐的全部股票（含 1/3/5日 + 至今收益率）。"""
    from app.services.workbench_service import get_run_detail, list_run_recommendations

    run = get_run_detail(db, run_id)
    if run is None:
        return None

    recs = list_run_recommendations(db, run_id)
    # 批量加载 K 线：按 code 分组，减少 DB 往返
    today = today_date or _latest_kbar_date(db) or run.get("trade_date") or ""

    # 收集所有 code+trade_date，批量加载
    codes = list({r["code"] for r in recs})
    bars_map: dict[str, list[DailyKbar]] = {}
    if codes:
        trade_dates = [r["trade_date"] for r in recs]
        min_date = min(trade_dates) if trade_dates else run.get("trade_date", "")
        rows = db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code.in_(codes), DailyKbar.trade_date >= min_date)
            .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
        ).all()
        by_code: dict[str, list[DailyKbar]] = {}
        for b in rows:
            by_code.setdefault(b.code, []).append(b)
        # 每只股票只取 >= 推荐日的部分
        for r in recs:
            code = r["code"]
            td = r["trade_date"]
            all_bars = by_code.get(code, [])
            # 过滤 >= trade_date
            r_bars = [b for b in all_bars if b.trade_date >= td]
            bars_map[code] = r_bars

    enriched = []
    for r in recs:
        bars = bars_map.get(r["code"], [])
        returns = _compute_returns(float(r.get("recommend_price") or 0), bars, today)
        enriched.append({**r, "returns": returns})

    return {
        "run": run,
        "recommendations": enriched,
        "as_of_date": today,
    }


def _latest_kbar_date(db: Session) -> str | None:
    """获取 daily_kbars 表中最新交易日（用于"至今"口径）。"""
    row = db.scalar(
        select(DailyKbar.trade_date).order_by(DailyKbar.trade_date.desc()).limit(1)
    )
    return row if row else None


def list_matrix_records(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    code: str | None = None,
) -> dict[str, Any]:
    """矩阵分析历史记录列表（分页，按 created_at 倒序）。

    与 stock_matrix.py 的 /history 不同：这里跨所有 code，按时间倒序。
    """
    stmt = select(StockMatrixHistory)
    count_stmt = select(func.count()).select_from(StockMatrixHistory)
    if code:
        stmt = stmt.where(StockMatrixHistory.code == code)
        count_stmt = count_stmt.where(StockMatrixHistory.code == code)

    total = db.scalar(count_stmt) or 0
    offset = (page - 1) * page_size
    rows = db.scalars(
        stmt.order_by(desc(StockMatrixHistory.created_at)).offset(offset).limit(page_size)
    ).all()

    items = [_serialize_matrix_row(r) for r in rows]
    return {
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "items": items,
    }


def get_matrix_record_detail(db: Session, record_id: int) -> dict[str, Any] | None:
    """单条矩阵分析记录详情。"""
    row = db.get(StockMatrixHistory, record_id)
    if row is None:
        return None
    data = _serialize_matrix_row(row)
    # result_json 存了完整的 skill 矩阵结果
    import json
    try:
        data["result"] = json.loads(row.result_json) if row.result_json else {}
    except Exception:
        data["result"] = {}
    # 附加该股票从分析日到今天的收益率（与推荐记录同口径）
    bars = list(
        db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code == row.code, DailyKbar.trade_date >= row.trade_date)
            .order_by(DailyKbar.trade_date.asc())
        ).all()
    )
    # 矩阵分析没有 recommend_price，用分析日 close 作为基准
    base_price = float(bars[0].close) if bars else 0.0
    today = _latest_kbar_date(db) or row.trade_date
    data["returns"] = _compute_returns(base_price, bars, today)
    data["base_price"] = base_price
    return data


def _serialize_matrix_row(row: StockMatrixHistory) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "trade_date": row.trade_date,
        "analysis_type": row.analysis_type,
        "final_score": row.final_score,
        "win_rate_est": row.win_rate_est,
        "avg_return_est": row.avg_return_est,
        "skill_count": row.skill_count,
        "elapsed_ms": row.elapsed_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
