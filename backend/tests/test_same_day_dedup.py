"""同日跨 run 重复推荐去重（T3 失败归因闭环）。

因果：多个 formal run 同日扫描全市场，常把同一只票重复推荐，造成「同一笔坏赌注被计
2~7 次亏损」的集中度/噪声问题。本测试验证两件事：
1. `_same_day_recommended_codes` 能正确查出当日已被其他 formal run 推荐过的 code；
2. `_drop_as_repeat_card` 的去重决策逻辑（dedup_same_day 与 hide_recent_repeats 正交）。
"""
from sqlalchemy import text

from app.services.workbench_service import (
    _drop_as_repeat_card,
    _same_day_recommended_codes,
)


def _find_same_day_multi_run_dup(db):
    """返回 (trade_date, code, run_id) —— 该 code 在当日被 >=2 个不同 formal run 推荐。"""
    rows = db.execute(
        text(
            """
        SELECT r.trade_date, r.code, COUNT(DISTINCT r.run_id) nruns
        FROM recommendations r
        JOIN recommendation_runs rr ON rr.run_id = r.run_id
        WHERE rr.run_mode = 'formal'
        GROUP BY r.trade_date, r.code
        HAVING COUNT(DISTINCT r.run_id) > 1
        LIMIT 1
        """
        )
    ).fetchone()
    if rows is None:
        return None
    td, code = rows[0], rows[1]
    run_id = db.execute(
        text(
            """
        SELECT r.run_id FROM recommendations r
        JOIN recommendation_runs rr ON rr.run_id = r.run_id
        WHERE rr.run_mode = 'formal' AND r.trade_date = :td AND r.code = :c
        LIMIT 1
        """
        ),
        {"td": td, "c": code},
    ).fetchone()[0]
    return td, code, run_id


def test_same_day_recommended_codes_finds_duplicates(db):
    found = _find_same_day_multi_run_dup(db)
    if found is None:
        # 数据库无同日跨 run 重复推荐样本（极少见），跳过而非误报
        import pytest

        pytest.skip("no same-day cross-run duplicate formal recs in DB")
    td, code, _ = found
    result = _same_day_recommended_codes(db, td)
    assert isinstance(result, set)
    assert code in result


def test_same_day_recommended_codes_excludes_own_run(db):
    found = _find_same_day_multi_run_dup(db)
    if found is None:
        import pytest

        pytest.skip("no same-day cross-run duplicate formal recs in DB")
    td, code, run_id = found
    # 排除当前 run 后，其他 run 仍推荐过该 code → 仍在集合内
    result = _same_day_recommended_codes(db, td, exclude_run_id=run_id)
    assert code in result


def test_drop_as_repeat_card_same_day():
    card = {"code": "603268"}
    params = {"dedup_same_day": True, "hide_recent_repeats": True}
    # 同日跨 run 重复 → 剔除
    assert _drop_as_repeat_card(card, params, recent_repeat=False, same_day_repeat=True) is True
    # 非重复 → 保留
    assert _drop_as_repeat_card(card, params, recent_repeat=False, same_day_repeat=False) is False


def test_drop_as_repeat_card_respects_dedup_same_day_toggle():
    card = {"code": "603268"}
    # 关闭 dedup_same_day 时，即便 same_day_repeat=True 也不因该规则剔除
    params_off = {"dedup_same_day": False, "hide_recent_repeats": True}
    assert _drop_as_repeat_card(card, params_off, recent_repeat=False, same_day_repeat=True) is False
    # 但 hide_recent_repeats 仍为 True 且 recent_repeat=True 时仍剔除（既有逻辑不受影响）
    assert _drop_as_repeat_card(card, params_off, recent_repeat=True, same_day_repeat=False) is True


def test_drop_as_repeat_card_no_code_safe():
    assert _drop_as_repeat_card({}, {"dedup_same_day": True}, recent_repeat=False, same_day_repeat=False) is False
