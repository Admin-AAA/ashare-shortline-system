import ast
from typing import Any


class UnsafeExpression(ValueError):
    pass


ALLOWED_NAMES = {
    "amount_yi",
    "announcement_negative_count",
    "breakout20",
    "breakout60",
    "capital_flow_score",
    "chg20",
    "chg60",
    "chip_concentration",
    "chip_health_score",
    "dragon_tiger_recent_count",
    "dist20",
    "dist60",
    "dsa_bias_score",
    "dsa_macd_score",
    "dsa_rsi_score",
    "dsa_signal_score",
    "dsa_support_score",
    "dsa_trend_score",
    "dsa_volume_score",
    "eff20",
    "data_completeness_score",
    "evidence_quality_score",
    "fundamental_score",
    "free_mv_yi",
    "gross_margin",
    "industry_capacity_like_count",
    "industry_positive_excess20_count",
    "inflow_5d_yi",
    "inflow_10d_yi",
    "intelligence_score",
    "is_st",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "main_net_inflow_yi",
    "max_dd20",
    "max_dd60",
    "net_profit_yoy",
    "news_risk_count",
    "off_high_pct",
    "pct",
    "pe_ttm",
    "positive_catalyst_count",
    "pos20",
    "pos60",
    "pos120",
    "profit_ratio",
    "primary_excess20",
    "primary_excess60",
    "resistance_distance_pct",
    "revenue_yoy",
    "roe",
    "rsi_6",
    "rsi_12",
    "rsi_24",
    "sector_fund_rank",
    "support_ma5",
    "support_ma10",
    "turnover_rate",
    "upper_shadow_count20",
    "vol5_20",
    "volume_breakout_signal",
    "volume_ratio_5d",
    "volume_shrink_pullback",
    "_is_main",
}


def evaluate(expression: str, context: dict[str, Any]) -> bool:
    if not expression:
        return False
    normalized = (
        expression.replace(" AND ", " and ")
        .replace(" OR ", " or ")
        .replace(" NOT ", " not ")
        .replace(" TRUE", " true")
        .replace(" FALSE", " false")
    )
    try:
        node = ast.parse(normalized, mode="eval")
        return bool(_eval(node.body, context))
    except Exception:
        return False


def _eval(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, context) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise UnsafeExpression("unsupported bool op")

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, context)

    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, context)
            if not _compare(left, op, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Name):
        name = node.id
        if name == "true":
            return True
        if name == "false":
            return False
        if name not in ALLOWED_NAMES:
            raise UnsafeExpression(f"name not allowed: {name}")
        return _coerce(context.get(name))

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise UnsafeExpression("only numeric and bool constants are allowed")

    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _eval(node.left, context)
        right = _eval(node.right, context)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right if right else 0

    raise UnsafeExpression(f"node not allowed: {type(node).__name__}")


def _compare(left: Any, op: ast.cmpop, right: Any) -> bool:
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    raise UnsafeExpression("unsupported comparison")


def _coerce(value: Any) -> Any:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
