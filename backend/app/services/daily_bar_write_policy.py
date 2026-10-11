"""daily_kbars 权威行情列的写入策略。

pct_chg / turnover_rate 在库里是 NOT NULL，"缺失" 无法用 NULL 表达，因此由本模块在
Python 侧判定当批是否真的带回了这两个数值：带回才放进 ON DUPLICATE KEY UPDATE，没带回
就整列退出 update，保留库中已有真值。实时快照在非交易时段或 provider 回落时不带涨跌幅与
换手率，若按 0 覆盖会把当日全表刷成恒零（L-146）。
"""

from dataclasses import dataclass
from typing import Any

# 由实时快照 / 日线同步带回的权威行情列，缺失时不得按 0 覆盖。
QUOTE_METRIC_COLUMNS = ("pct_chg", "turnover_rate")

# daily_kbars.volume 与 Tushare daily 同单位（手）；实时快照 realtime_quote 的 VOLUME 单位是股。
SHARES_PER_LOT = 100

# 只在内存里拼给当次运行使用的临时日K来源。它们是盘中半截数据（成交量按股、换手率缺失、
# 涨跌幅相对的昨收可能就是当日自身），任何写库路径都不得把它们写进 daily_kbars（L-159）。
TRANSIENT_BAR_SOURCES = frozenset({"realtime_quote"})


def is_transient_bar(bar: dict[str, Any]) -> bool:
    """当次运行临时拼出的日K，不是收盘后的权威日线。"""
    return str(bar.get("source") or "") in TRANSIENT_BAR_SOURCES

# 整批判定数据源未就绪的最小行数。逐股票补数（停牌、新股）出现全 0 属正常，不能误杀。
UNREADY_BATCH_MIN_ROWS = 100

# 整批非零涨跌幅占比低于此值即判定数据源未就绪。真实交易日绝大多数股票都有非零涨跌幅
# （平盘、停牌只占少数），而盘前/休市快照往往整片为 0；只要求「一个非零都没有」不够，
# 少数股票残留非零值就会让其余多数被 0 覆盖（L-147）。
UNREADY_NONZERO_RATIO = 0.2


def normalize_quote_metric(value: Any) -> float | None:
    """把权威行情列的取值规范化：缺失返回 None，真实数值（含 0）返回 float。"""
    if value in {None, "", "-"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def quote_metrics_look_unready(rows: list[dict[str, Any]]) -> bool:
    """非零涨跌幅占比过低时判定数据源未就绪（与日线批量同步的既有防线同口径）。"""
    if len(rows) < UNREADY_BATCH_MIN_ROWS:
        return False
    nonzero = sum(1 for row in rows if normalize_quote_metric(row.get("pct_chg")))
    return (nonzero / len(rows)) < UNREADY_NONZERO_RATIO


@dataclass(frozen=True)
class DailyBarWriteGroup:
    """一组写入行，以及其中哪些权威行情列真的带回了数值。"""

    rows: list[dict[str, Any]]
    present_columns: tuple[str, ...]

    def quote_metric_update(self, stmt: Any) -> dict[str, Any]:
        """构造该组的 ON DUPLICATE KEY UPDATE 片段，只含真的带回数值的列。"""
        return {name: getattr(stmt.inserted, name) for name in self.present_columns}


def group_rows_by_quote_metric_presence(
    rows: list[dict[str, Any]],
) -> list[DailyBarWriteGroup]:
    """按权威行情列的到位情况分组，并把 None 补成 0 以满足列的 NOT NULL 约束。

    新插入的行没有可保留的旧值，只能落 0，交由补数路径填真值；已存在的行则通过
    退出 update 保住原值。
    """
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        present: list[str] = []
        for name in QUOTE_METRIC_COLUMNS:
            value = normalize_quote_metric(row.get(name))
            if value is None:
                row[name] = 0.0
            else:
                row[name] = value
                present.append(name)
        grouped.setdefault(tuple(present), []).append(row)
    return [
        DailyBarWriteGroup(rows=group_rows, present_columns=present)
        for present, group_rows in grouped.items()
    ]
