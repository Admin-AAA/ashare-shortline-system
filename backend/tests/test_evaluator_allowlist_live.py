"""L-187：现役策略 yaml 里的每个表达式名字都必须在 evaluator 白名单内（否则静默恒 False）。"""
import ast
from pathlib import Path

import yaml

from app.engine import evaluator as E
from app.services import standalone_strategies as ss

SKILLS = Path(__file__).resolve().parents[1] / "skills"
_LITERALS = {"true", "false", "True", "False", "None", "null"}


def _exprs(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "when" and isinstance(v, str):
                yield v
            else:
                yield from _exprs(v)
    elif isinstance(o, list):
        for v in o:
            if isinstance(v, str) and any(op in v for op in ("<", ">", "==", "!=")):
                yield v  # universe 条件列表；纯说明文字（risk_flags 等）不算表达式
            else:
                yield from _exprs(v)


def test_live_strategy_names_allowed():
    try:
        from app.services.factors import bootstrap_factor_allowlist
        bootstrap_factor_allowlist()
    except Exception:  # noqa: BLE001
        pass
    bad = []
    for d in sorted(SKILLS.iterdir()):
        y = d / "strategy.yaml"
        if not y.exists() or d.name in ss.RETIRED_STRATEGY_IDS or d.name.startswith("research-"):
            continue
        for e in _exprs(yaml.safe_load(y.read_text(encoding="utf-8"))):
            try:
                tree = ast.parse(e, mode="eval")
            except SyntaxError:
                continue  # 说明文字，不是表达式
            for n in ast.walk(tree):
                if isinstance(n, ast.Name) and n.id not in E.ALLOWED_NAMES and n.id not in _LITERALS:
                    bad.append((d.name, n.id, e[:60]))
    assert not bad, bad


def test_price_comparison_works():
    assert E.evaluate("price >= ma20", {"price": 10.0, "ma20": 9.0}, strict_missing=True)
    assert E.evaluate("pledge_ratio >= 40", {"pledge_ratio": 45.0}, strict_missing=True)
