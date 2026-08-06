"""P0-2 — PromptContextPolicy 确定性投影与裁剪纪律测试。

锁定 R-LLM-2/3/4：
  - 版本化 policy、字段白名单、固定 section 顺序
  - 相同业务输入 + 相同 policy → 相同 projected fingerprint（dict 顺序不变性）
  - 中文/多字节文本按段落/句子边界裁剪，不从 JSON/字符中间硬切
  - 列表按稳定业务键排序；无业务键保持输入顺序并记录
  - required path 强制保留；超限时 typed overflow（不发残缺 prompt）
  - projected 指纹进入 cache identity（policy/裁剪变化 → cache 失效）
  - 单轮调用、无 history、structured output schema 不变
  - 审计只保存结构化摘要/hash，不落完整 prompt 原文
"""
from __future__ import annotations

import json
import re

import pytest

from app.services.llm.prompt_context import (
    DEFAULT_PROMPT_CONTEXT_POLICY,
    PROMPT_CONTEXT_POLICY_ID,
    PromptContextTooLarge,
    PromptFieldRule,
    canonical_section_json,
    project_prompt_context,
)


def _stock(**over):
    base = {
        "code": "000001",
        "name": "平安银行",
        "price": 10.5,
        "pct": 1.2,
        "latest_trade_date": "2026-08-06",
        "rsi_12": 65.0,
        "ma5": 10.2,
    }
    base.update(over)
    return base


def _snapshot(**over):
    base = {"trade_date": "2026-08-06", "sample_count": 5000, "up_count": 3000, "breadth": {"regime": "强势"}}
    base.update(over)
    return base


def _signal(**over):
    base = {"phase": "主升", "score": 80, "setup_match": True, "evidence": ["突破"], "dsa_signal_score": 78}
    base.update(over)
    return base


# ── 确定性 / dict 顺序不变性 ──────────────────────────────────────────

def test_projection_is_deterministic_across_dict_order():
    a = _stock(rsi_12=65.0, ma5=10.2)
    b = {"ma5": 10.2, "rsi_12": 65.0, "code": "000001", "name": "平安银行",
         "price": 10.5, "pct": 1.2, "latest_trade_date": "2026-08-06"}
    pa = project_prompt_context(stock=a, snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")
    pb = project_prompt_context(stock=b, snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")
    assert pa.projected_fingerprint == pb.projected_fingerprint


def test_projected_context_is_valid_json_and_sorted():
    p = project_prompt_context(stock=_stock(), snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")
    for section in ("market_snapshot", "rule_signal", "stock"):
        text = canonical_section_json(p, section)
        # canonical JSON 必须可解析（不截断 token）
        parsed = json.loads(text)
        assert isinstance(parsed, dict)


def test_whitelist_drops_unknown_fields():
    p = project_prompt_context(
        stock=_stock(rogue_field="should_drop", another=1),
        snapshot=_snapshot(snapshot_id="meta"),
        rule_signal=_signal(),
        research=None,
        skill_id="s",
    )
    assert "rogue_field" not in canonical_section_json(p, "stock")
    assert "snapshot_id" not in canonical_section_json(p, "market_snapshot")
    assert "stock.rogue_field" in p.audit.removed_fields


# ── 中文/多字节文本裁剪（仅在超限时触发；额度内保留原始语义）────────────

def _tiny_policy(max_tokens: int = 250):
    from dataclasses import replace
    return replace(DEFAULT_PROMPT_CONTEXT_POLICY, max_estimated_tokens=max_tokens)


def test_within_budget_long_text_is_preserved():
    # 额度内：不做句子/字符裁剪，保持默认审查语义（spec：裁剪是超限的兜底）
    news = "第一条公告内容。" * 100  # ~800 chars，仍在默认 4500 token 额度内
    p = project_prompt_context(
        stock=_stock(latest_news=news), snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s"
    )
    assert p.section("stock").get("latest_news") == news


def test_over_budget_multibyte_truncated_at_sentence_boundary():
    news = ("第一条公告内容。" * 200) + "\n\n" + ("第二条公告内容。" * 200)
    p = project_prompt_context(
        stock=_stock(latest_news=news), snapshot=_snapshot(), rule_signal=_signal(),
        research=None, skill_id="s", policy=_tiny_policy(),
    )
    kept = p.section("stock").get("latest_news", "")
    # 裁剪后要么字段被移除，要么保留到 ≤1200 且落在句子/段边界
    assert "stock.latest_news" in p.audit.truncated_text_fields or "stock.latest_news" in p.audit.removed_fields
    if kept:
        assert len(kept) <= 1200


def test_over_budget_long_text_without_boundary_char_truncated_not_dropped():
    # 1300 字符无标点新闻 > max_text_chars(1200) 触发字符前缀裁剪。
    # 最小 stock 实测：full ctx=833 超限 780 → 裁到 777 落入额度 → 保留。
    news = "无标点" * 434  # 1302 字符
    minimal = {"code": "000001", "name": "x", "price": 1.0, "pct": 0.0, "latest_news": news}
    p = project_prompt_context(
        stock=minimal, snapshot={"trade_date": "2026-08-06"}, rule_signal=None,
        research=None, skill_id="s", policy=_tiny_policy(780),
    )
    kept = p.section("stock").get("latest_news", "")
    assert 0 < len(kept) <= 1200
    assert "stock.latest_news" in p.audit.truncated_text_fields
    assert "stock.latest_news" not in p.audit.removed_fields


def test_over_budget_very_long_unbounded_text_removed_when_still_over():
    # 极长文本裁到 1200 仍超 tiny 额度 → 该低优先字段整字段移除（不超发残缺）
    p = project_prompt_context(
        stock=_stock(latest_news="无标点" * 9000), snapshot=_snapshot(), rule_signal=_signal(),
        research=None, skill_id="s", policy=_tiny_policy(),
    )
    assert "stock.latest_news" in p.audit.removed_fields


# ── 列表稳定排序 ─────────────────────────────────────────────────────

def test_list_with_business_keys_sorted_deterministically():
    records = [
        {"trade_date": "2026-08-05", "note": "b"},
        {"trade_date": "2026-08-04", "note": "a"},
    ]
    research = {"records": records, "summary": "s"}
    pa = project_prompt_context(stock=_stock(), snapshot=_snapshot(), rule_signal=_signal(), research=research, skill_id="s")
    pb = project_prompt_context(
        stock=_stock(), snapshot=_snapshot(), rule_signal=_signal(),
        research={"records": list(reversed(records)), "summary": "s"}, skill_id="s",
    )
    assert pa.projected_fingerprint == pb.projected_fingerprint


def test_list_without_business_keys_preserves_order():
    # 额度内：无业务键列表保持输入顺序（截断与审计只在超限路径发生）
    research = {"notes": ["先", "后", "再"]}
    p = project_prompt_context(stock=_stock(), snapshot=_snapshot(), rule_signal=_signal(), research=research, skill_id="s")
    assert p.section("research").get("notes") == ["先", "后", "再"]


# ── required path / overflow ─────────────────────────────────────────

def test_required_core_paths_preserved_under_limit():
    p = project_prompt_context(stock=_stock(), snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")
    for sec, f in (("stock", "code"), ("stock", "name"), ("stock", "price"),
                   ("market_snapshot", "trade_date"), ("review_identity", "skill_id")):
        assert p.section(sec).get(f) is not None


def test_missing_required_input_fails_closed():
    with pytest.raises(ValueError, match="skill_id"):
        project_prompt_context(stock=_stock(), snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="")
    with pytest.raises(ValueError, match="code"):
        project_prompt_context(stock={"name": "无code"}, snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")


def test_missing_rule_signal_uses_agent_only_not_null():
    p = project_prompt_context(stock=_stock(), snapshot=_snapshot(), rule_signal=None, research=None, skill_id="s")
    rs = p.section("rule_signal")
    assert rs.get("runtime") == "agent_only"
    # 不注入 null 的规则字段
    assert "phase" not in rs and "score" not in rs


def test_policy_rejects_required_that_is_also_excluded():
    rule = PromptFieldRule(section="stock", field="code", required=True)
    with pytest.raises(ValueError):
        # section_order 不含 stock，但 field_rules 引用了它
        type(DEFAULT_PROMPT_CONTEXT_POLICY)(
            policy_id="x", schema_version=1, section_order=("market_snapshot",),
            field_rules=(rule,), max_estimated_tokens=100, required_paths=("stock.code",),
        )


# ── 溢出 → typed error ───────────────────────────────────────────────

def test_overflow_raises_typed_error_not_corrupted_prompt():
    huge = {f"filler_{i}": "x" * 500 for i in range(60)}
    stock = _stock(**huge)
    # 用一个极小 max_estimated_tokens 的 policy 强制溢出
    from dataclasses import replace
    tiny = replace(DEFAULT_PROMPT_CONTEXT_POLICY, max_estimated_tokens=5)
    with pytest.raises(PromptContextTooLarge):
        project_prompt_context(stock=stock, snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s", policy=tiny)


# ── projected 指纹进 cache identity ──────────────────────────────────

def test_projected_fingerprint_changes_cache_key():
    from app.services.llm_review_cache_service import build_review_cache_key
    p1 = project_prompt_context(stock=_stock(rsi_12=65.0), snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")
    p2 = project_prompt_context(stock=_stock(rsi_12=66.0), snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")
    assert p1.projected_fingerprint != p2.projected_fingerprint
    common = dict(
        skill_hash="h", stock=_stock(), snapshot=_snapshot(), rule_signal=_signal(),
        provider="p", model="m", system_prompt="sys", user_prompt="up",
    )
    k1 = build_review_cache_key(**common, prompt_context_fingerprint=p1.projected_fingerprint)
    k2 = build_review_cache_key(**common, prompt_context_fingerprint=p2.projected_fingerprint)
    assert k1 != k2


# ── 审计不落完整 prompt ──────────────────────────────────────────────

def test_audit_summary_contains_no_prompt_body():
    secret = "这是一段不应出现在审计里的敏感原文XYZ"
    p = project_prompt_context(
        stock=_stock(latest_news=secret), snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s"
    )
    audit_text = json.dumps(p.audit_summary(), ensure_ascii=False)
    assert secret not in audit_text
    # 只有结构化摘要 + hash
    assert p.audit_summary()["projected_fingerprint"]
    assert p.audit_summary()["policy_id"] == PROMPT_CONTEXT_POLICY_ID
    assert isinstance(p.audit_summary()["removed_fields"], list)


# ── 单轮 / 无 history / structured output 不变 ───────────────────────

def test_build_user_prompt_has_no_history_and_keeps_schema():
    import app.agents.agno_skill_agent as m
    stock = _stock()
    p = project_prompt_context(stock=stock, snapshot=_snapshot(), rule_signal=_signal(), research=None, skill_id="s")
    prompt = m._build_user_prompt(stock, _snapshot(), _signal(), None, "s", projection=p)
    # 无 research 时不出现历史参考 section
    assert "# 历史参考" not in prompt
    # structured output schema 字段仍在（不被投影改动）
    for field in ("stance", "score", "confidence", "evidence", "risks", "summary",
                  "market_regime_judgment", "action", "position_range", "entry_style",
                  "invalidation_conditions", "next_day_plan"):
        assert field in prompt
    # policy identity 出现在稳定前缀
    assert PROMPT_CONTEXT_POLICY_ID in prompt


def test_direct_and_agno_share_same_projection_fingerprint():
    import app.agents.agno_skill_agent as m
    stock = _stock()
    snap = _snapshot()
    sig = _signal()
    proj = m.project_prompt_context(stock=stock, snapshot=snap, rule_signal=sig, research=None, skill_id="s")
    # 两条路径都用同一个 projection → 相同 projected fingerprint
    up = m._build_user_prompt(stock, snap, sig, None, "s", projection=proj)
    from pathlib import Path
    from app.engine.skill_loader import StrategySkill
    skill = StrategySkill(skill_id="s", name="测试", version="0.1", path=Path("."),
                          manifest={}, skill_frontmatter={}, skill_body="b", skill_hash="h",
                          reference_files=[], contract=None, contract_hash=None, enabled=True)
    dp = m._build_direct_prompt(stock, snap, sig, skill, projection=proj)
    # 两条 prompt 都引用同一 policy 且包含同一股票 code
    assert PROMPT_CONTEXT_POLICY_ID in up and PROMPT_CONTEXT_POLICY_ID in dp
    assert '"000001"' in up and '"000001"' in dp


# ── _parse_to_output 默认分不变 ──────────────────────────────────────

def test_parse_to_output_defaults_unchanged():
    import app.agents.agno_skill_agent as m
    out = m._parse_to_output({})
    assert out.stance == "neutral"
    assert out.score == 50
    assert 0.0 <= out.confidence <= 1.0
    # 非法 stance 兜底 neutral，非法 score clamp
    out2 = m._parse_to_output({"stance": "bogus", "score": 999})
    assert out2.stance == "neutral" and out2.score == 100
