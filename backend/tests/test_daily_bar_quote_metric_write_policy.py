"""权威行情列（pct_chg / turnover_rate）缺失时不得按 0 覆盖真值。

背景数据墙：2026-09-24 参考交易日 4950 行的 pct_chg 与 turnover_rate 全为 0，而同日
close 与真实收盘逐只一致、volume 恰为 Tushare `vol` 的 100 倍——这组单位指纹指认写入者是
「把实时快照提升成当日日K」的路径，它用 `_num(item.get("pct"))` 取值，快照不带这两个字段
时得 0，随后 upsert 无条件覆盖，当日全表被抹平。健康度因此从 96 掉到 87（两列 critical
各扣 5）。这两列在库里是 NOT NULL，"缺失" 无法用 NULL 表达，故由写入策略在 Python 侧判定
当批是否真的带回数值。

本测试锁定四件事：
  1. 缺失（None）与真实 0 必须被区分开
  2. 当批没带回数值时，该列整列退出 UPDATE，保住库中真值
  3. 当批带回数值时仍然直接覆盖（不能因保护而失去更新能力）
  4. 整批都没有非零涨跌幅时判定数据源未就绪
"""
from __future__ import annotations

from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.tables import DailyKbar
from app.services.daily_bar_write_policy import (
    QUOTE_METRIC_COLUMNS,
    UNREADY_BATCH_MIN_ROWS,
    group_rows_by_quote_metric_presence,
    normalize_quote_metric,
    quote_metrics_look_unready,
)


def _update_clause(group) -> str:
    stmt = mysql_insert(DailyKbar).values(group.rows)
    final = stmt.on_duplicate_key_update(
        close=stmt.inserted.close,
        **group.quote_metric_update(stmt),
    )
    sql = str(final.compile(dialect=mysql.dialect()))
    return sql.split("ON DUPLICATE KEY UPDATE", 1)[1].lower()


def test_missing_and_real_zero_are_different():
    """缺失返回 None，真实 0 返回 0.0——区分这两者是整条防线的前提。"""
    assert normalize_quote_metric(None) is None
    assert normalize_quote_metric("") is None
    assert normalize_quote_metric("-") is None
    assert normalize_quote_metric("abc") is None
    assert normalize_quote_metric(0) == 0.0
    assert normalize_quote_metric("0") == 0.0
    assert normalize_quote_metric(-1.85) == -1.85


def test_absent_quote_metrics_exit_the_update_clause():
    """快照没带回这两列时，它们必须整列退出 UPDATE，库中真值得以保留。"""
    rows = [
        {
            "code": "000001",
            "trade_date": "2026-09-24",
            "close": 11.3,
            "pct_chg": None,
            "turnover_rate": None,
        }
    ]
    groups = group_rows_by_quote_metric_presence(rows)
    assert len(groups) == 1
    assert groups[0].present_columns == ()
    upd = _update_clause(groups[0])
    assert "close = values(close)" in upd
    for col in QUOTE_METRIC_COLUMNS:
        assert f"{col} = values({col})" not in upd, f"{col} 缺失时不得进入 UPDATE"


def test_absent_quote_metrics_still_satisfy_not_null_on_insert():
    """列是 NOT NULL，新行只能落 0；已存在的行靠退出 UPDATE 保值。"""
    rows = [{"code": "000001", "trade_date": "2026-09-24", "pct_chg": None, "turnover_rate": None}]
    group_rows_by_quote_metric_presence(rows)
    assert rows[0]["pct_chg"] == 0.0
    assert rows[0]["turnover_rate"] == 0.0


def test_present_quote_metrics_still_overwrite():
    """带回数值时仍直接覆盖，包括真实的 0（平盘）。"""
    rows = [
        {
            "code": "000001",
            "trade_date": "2026-09-24",
            "close": 11.3,
            "pct_chg": 0.0,
            "turnover_rate": 0.54,
        }
    ]
    groups = group_rows_by_quote_metric_presence(rows)
    assert groups[0].present_columns == QUOTE_METRIC_COLUMNS
    upd = _update_clause(groups[0])
    for col in QUOTE_METRIC_COLUMNS:
        assert f"{col} = values({col})" in upd, f"{col} 带回数值时应直接覆盖"


def test_rows_are_grouped_by_which_columns_arrived():
    """同一批里到位情况不同的行分开写，互不牵连。"""
    rows = [
        {"code": "000001", "trade_date": "2026-09-24", "pct_chg": -0.44, "turnover_rate": 0.54},
        {"code": "000002", "trade_date": "2026-09-24", "pct_chg": -1.2, "turnover_rate": None},
        {"code": "000003", "trade_date": "2026-09-24", "pct_chg": None, "turnover_rate": None},
    ]
    groups = {group.present_columns: group for group in group_rows_by_quote_metric_presence(rows)}
    assert set(groups) == {("pct_chg", "turnover_rate"), ("pct_chg",), ()}
    assert len(groups[("pct_chg",)].rows) == 1
    assert groups[("pct_chg",)].rows[0]["code"] == "000002"


def test_all_zero_batch_is_judged_source_not_ready():
    """非零涨跌幅占比过低=数据源未就绪；小批量全 0（停牌/新股）不得误杀。"""
    big_zero_batch = [
        {"code": f"{index:06d}", "trade_date": "2026-09-24", "pct_chg": 0.0}
        for index in range(UNREADY_BATCH_MIN_ROWS)
    ]
    assert quote_metrics_look_unready(big_zero_batch) is True

    small_zero_batch = big_zero_batch[: UNREADY_BATCH_MIN_ROWS - 1]
    assert quote_metrics_look_unready(small_zero_batch) is False

    healthy_batch = [
        {**row, "pct_chg": -1.85 if index % 10 else 0.0}
        for index, row in enumerate(big_zero_batch)
    ]
    assert quote_metrics_look_unready(healthy_batch) is False


def test_mostly_zero_batch_with_a_few_stale_values_is_still_not_ready():
    """盘前快照少数股票残留非零值时，整批仍必须判未就绪。

    背景数据墙：2026-09-28 00:03 两个 run 把 09-24 全部 4951 行的 pct_chg 与
    turnover_rate 写成精确 0.0。只要求「一个非零都没有」的防线挡不住这种形态——
    少数残留非零值就会让其余多数被 0 覆盖。
    """
    rows = [
        {"code": f"{index:06d}", "trade_date": "2026-09-24", "pct_chg": 0.0}
        for index in range(1000)
    ]
    for index in range(50):  # 5% 残留非零
        rows[index]["pct_chg"] = -1.2
    assert quote_metrics_look_unready(rows) is True
