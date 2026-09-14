"""把 14 个 skill 里**可调的字段**逐一分类：哪些已被既有教训封死、哪些还没测。

为什么需要它：候选生成层重建（`research_candidate_pool_rebuild.py`）的真正用途是
测"改某个 skill 的 universe / 规则 → 候选池不同"。但如果每一个可调字段都落在
已被证伪的族里，那重建就只能用来重测已封方向（违反 L-009 的"不重复同一组参数"）。
所以开工前先把字段清单与教训做一次机械比对。

做法：扫 `skills/*/strategy.yaml` 中 universe.include / exclude / setups / scoring /
phases / risk 等所有条件表达式，抽出其中的标识符，去掉 YAML 结构词与常量，
再按 LESSONS 的既有封禁族打标签。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_skill_knob_classification.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]

# 既有教训封禁的族（键=族名，值=(lesson, 字段/正则列表)）
SEALED = {
    "L-061 价格技术横截面": (
        "L-061", ["score", "chg5", "chg20", "chg60", "pos60", "vol5_20", "vol_ratio_5",
                  "vol_ratio_20", "sig_gap", "pct", "rsi_6", "rsi_12", "rsi_24", "macd",
                  "bias_5", "bias_10", "bias_20", "ma5", "ma10", "ma20", "ma60",
                  "support_ma5", "off_high_pct", "max_dd20", "max_dd60", "turnover_rate",
                  "amount_yi", "close", "high", "low", "open", "volume", "breakout20",
                  "breakout60", "upper_shadow_count20"],
    ),
    "L-075 规模/估值/换手水平/封板属性": (
        "L-075", ["circ_mv", "total_mv", "pe_ttm", "pb", "ps_ttm", "dv_ratio",
                  "limit_times", "open_times", "fd_amount", "first_time", "last_time",
                  "limit_up_overheat", "limit_up_overheat_score", "limit_up_strength",
                  "limit_up_days", "limit_up_consecutive_days", "limit_up_open_times",
                  "limit_up_fd_amount_yi", "limit_up_first_time", "limit_up_last_time",
                  "limit_up_turnover_ratio", "limit_up_count", "is_limit_up"],
    ),
    "L-081 板块/行业横截面": (
        "L-081", ["industry", "concept_sector_name", "rs5", "sec_", "sector_"],
    ),
    "L-082 stk_limits 板型/距涨停": (
        "L-082", ["limit_up_price", "limit_down_price", "up_limit", "down_limit"],
    ),
    "L-075(b) 已知空列/未计算列": (
        "L-075/081", ["profit_efficiency", "atr", "holder_pct"],
    ),
}
# 明确不在上表、值得单独看的"语义类"字段（候选生成可能真正改得动的）
SEMANTIC = [
    "strategy_signals", "raw_component_scores", "final_score", "evidence_quality_score",
    "data_completeness_score", "data_authenticity", "review_status", "is_main_theme",
    "market_regime", "market_sentiment_phase", "sentiment_phase", "market_base",
    "main_industries", "shortline_leader_score", "sector_ladder_completeness_score",
    "leader", "limit_up_reseal_relay", "twenty_cm_risk_gate", "dragon_tiger_",
    "news_risk_count", "announcement_negative_count", "positive_catalyst_count",
    "capital_flow_score", "holder_num_decreasing", "holder_trade_net_ratio",
]
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
STRUCT = {"and", "or", "not", "in", "true", "false", "when", "name", "include", "exclude",
          "score_cap", "hard_veto", "risk_flags", "score_delta", "delta", "priority",
          "reason", "label", "weight", "min", "max"}


def _fields(text: str) -> set:
    return {t for t in IDENT.findall(text) if t not in STRUCT and not t.isdigit()}


def main() -> None:
    skills = sorted(p for p in (B / "skills").iterdir() if p.is_dir())
    per_skill, universe_all = {}, set()
    for d in skills:
        yml = d / "strategy.yaml"
        if not yml.exists():
            continue
        body = yml.read_text(encoding="utf-8")
        f = _fields(body)
        per_skill[d.name] = f
        universe_all |= f
    print(f"扫描 {len(per_skill)} 个 skill，条件表达式出现的标识符合计 {len(universe_all)} 个\n")

    sealed_hits, unsealed = {}, []
    for name in sorted(universe_all):
        for fam, (lesson, keys) in SEALED.items():
            if any(name == k or name.startswith(k) for k in keys):
                sealed_hits.setdefault(fam, []).append(name)
                break
        else:
            unsealed.append(name)

    print("=== 已被教训封禁的字段（按族）===")
    for fam, names in sorted(sealed_hits.items()):
        lesson = SEALED[fam][0]
        print(f"  [{lesson}] {fam}: {len(names)} 个 → {', '.join(sorted(names)[:14])}"
              + (" …" if len(names) > 14 else ""))
    covered = sum(len(v) for v in sealed_hits.values())
    print(f"  → 封禁族覆盖 {covered} / {len(universe_all)} = {covered/len(universe_all)*100:.0f}%\n")

    print("=== 未被封禁族覆盖的标识符（需要人工判定是不是真旋钮）===")
    semantic = [n for n in unsealed if any(n.startswith(s) or n == s for s in SEMANTIC)]
    other = [n for n in unsealed if n not in semantic]
    print(f"  A. 语义/规则级（候选生成真正可能改得动）：{len(semantic)}")
    print("     " + ", ".join(sorted(semantic)))
    print(f"  B. 其他（多为函数名/YAML 键/常量，需逐个确认）：{len(other)}")
    print("     " + ", ".join(sorted(other)[:60]) + (" …" if len(other) > 60 else ""))


if __name__ == "__main__":
    main()
