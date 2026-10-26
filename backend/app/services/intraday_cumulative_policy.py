"""全日累计量在盘中的可判定性政策。

策略 yaml 里 ``amount_yi >= 3``、``turnover_rate >= 1.5`` 这类门槛是按**全日**成交额
/换手率的回测样本校准出来的。推荐在盘后生成时，这些字段确实是全日值；但次日实时买入
确认发生在交易时段内，同名字段的含义变成「开盘至当前的累计值」。用盘中累计值去比全日
门槛，开盘几分钟内几乎每只票都低于下限，于是原本只该拦掉真正不合格标的的规则，把全部
待确认推荐一律判成取消。

当日累计量在盘中只增不减，由此得到一个不需要任何经验系数的判据：

- 下限类规则（``>= 3``）：累计值已达标 → 全日必达标 → 可以确定通过；累计值未达标 →
  全日仍可能达标 → **盘中不可判定**，不能据此取消。
- 上限类规则（``< 15``）：累计值已越界 → 全日必越界 → 可以确定否决；未越界 → 不可判定。

判定手段不解析表达式语义：把表达式里出现的累计类字段按「全日值 ∈ [当前值, +∞)」取若干
探测点重算，只要存在一个可达值能让规则通过，当前这次不通过就属于盘中不可判定。
"""

from __future__ import annotations

from typing import Any

from app.engine.evaluator import evaluate


# 当日累计、盘中单调不减的字段。主力净流入、封单额会回落，不属于此类，不登记。
SESSION_CUMULATIVE_FIELDS = frozenset({
    "amount",
    "amount_yi",
    "turnover_rate",
    "volume",
    "vol",
    "vol_ratio_5",
    "vol_ratio_20",
    "vol5_20",
    "volume_ratio_5d",
    "volume_ratio_vs_5d",
    "volume_ratio_vs_20d",
})

# 全日值相对当前累计值的探测倍数，加一个远超任何门槛的开区间上界探测点。
_PROBE_MULTIPLIERS = (2.0, 10.0, 100.0)
_PROBE_UNBOUNDED = 1e12


def cumulative_fields_in(observed: dict[str, Any]) -> dict[str, float]:
    """取出观测值里属于当日累计类且为数值的字段。"""
    return {
        name: float(value)
        for name, value in observed.items()
        if name in SESSION_CUMULATIVE_FIELDS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }


def intraday_cumulative_inconclusive(prefilter_detail: Any) -> bool:
    """这条未通过的策略条件是否只是「盘中累计量还没到」，收盘前不可判定。"""
    if not isinstance(prefilter_detail, dict) or prefilter_detail.get("passed"):
        return False
    expression = str(prefilter_detail.get("expression") or "")
    observed = prefilter_detail.get("observed")
    if not expression or not isinstance(observed, dict):
        return False
    cumulative = cumulative_fields_in(observed)
    if not cumulative:
        return False
    for probe in _probe_contexts(observed, cumulative):
        if evaluate(expression, probe, strict_missing=True):
            return True
    return False


def hard_veto_intraday_inconclusive(
    skill: Any,
    stock: dict[str, Any],
    main_industries: list[str],
) -> bool:
    """策略硬否决是否只是「盘中累计量还没到」：与 ``intraday_cumulative_inconclusive`` 同一判据。

    硬否决来自 strategy.yaml 的阶段规则 / 观察类 setup / require_setup，不止一条表达式，这里不解析
    规则，而是把当日累计类字段按「全日值 ∈ [当前值, +∞)」取同一组探测点重跑原策略：只要存在一个
    可达的全日值能让硬否决消失，当前这次否决就属于盘中不可判定，收盘前不据此给出退出结论。
    """
    from app.engine.strategy_runner import run_strategy

    cumulative = cumulative_fields_in(stock)
    if not cumulative:
        return False
    for probe in _probe_contexts(stock, cumulative):
        signal = run_strategy(skill, probe, list(main_industries or []), skip_universe_check=True)
        if not bool(signal.get("hard_veto")):
            return True
    return False


def _probe_contexts(
    observed: dict[str, Any],
    cumulative: dict[str, float],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for multiplier in _PROBE_MULTIPLIERS:
        contexts.append({
            **observed,
            **{name: value * multiplier for name, value in cumulative.items()},
        })
    contexts.append({
        **observed,
        **{name: _PROBE_UNBOUNDED for name in cumulative},
    })
    return contexts


__all__ = [
    "SESSION_CUMULATIVE_FIELDS",
    "cumulative_fields_in",
    "hard_veto_intraday_inconclusive",
    "intraday_cumulative_inconclusive",
]
