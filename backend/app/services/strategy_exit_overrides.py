"""逐策略出场微调（Sprint3 §67）。

策略在 strategy.yaml 里声明 `exit_overrides`（目前只开放 trailing 的利润地板参数），
回测（policy_experiment._simulate_card）与实盘结算（outcome_simulation）都按卡片的
「出场归属策略」套用，其余出场/费用/仓位仍用全局 execution policy。

归属策略 = 卡片 exit_strategy_id（aggregator 写入，=最高分 setup 的策略）；
旧卡片/面板缺该字段时，取非否决 strategy_signals 中分数最高者（与 aggregator best_setup 同口径）。
"""
from __future__ import annotations

import dataclasses as dc
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
# 只允许 trailing 利润地板相关字段，防止策略 yaml 意外改动止损/费用等风控口径。
ALLOWED_TRAILING_FIELDS = ("minimum_lock_gain_pct", "activation_gain_pct", "profit_lock_ratio")


@lru_cache(maxsize=1)
def load_exit_overrides() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for path in sorted(SKILLS_DIR.glob("*/strategy.yaml")):
        try:
            contract = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 坏 yaml 由 skill_loader 报错，这里只是不加覆盖
            continue
        raw = contract.get("exit_overrides") or {}
        if not isinstance(raw, Mapping):
            continue
        clean = {k: float(raw[k]) for k in ALLOWED_TRAILING_FIELDS if raw.get(k) is not None}
        if clean:
            out[str(contract.get("id") or path.parent.name)] = clean
    return out


def exit_strategy_id(card: Mapping[str, Any] | None) -> str | None:
    if not card:
        return None
    sid = card.get("exit_strategy_id")
    if sid:
        return str(sid)
    best: tuple[float, str] | None = None
    for sig in card.get("strategy_signals") or ():
        if not isinstance(sig, Mapping) or sig.get("hard_veto"):
            continue
        sid = sig.get("strategy_id")
        if not sid:
            continue
        score = float(sig.get("score") or 0)
        if best is None or score > best[0]:
            best = (score, str(sid))
    return best[1] if best else None


def exit_overrides_for(strategy_id: str | None) -> dict[str, float]:
    if not strategy_id:
        return {}
    return dict(load_exit_overrides().get(str(strategy_id), {}))


def apply_exit_overrides(policy, strategy_id: str | None):
    ov = exit_overrides_for(strategy_id)
    if not ov:
        return policy
    return dc.replace(policy, trailing=dc.replace(policy.trailing, **ov))


__all__ = ["apply_exit_overrides", "exit_overrides_for", "exit_strategy_id", "load_exit_overrides"]
