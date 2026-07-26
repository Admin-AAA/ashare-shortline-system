from app.services.llm_review_cache_service import build_review_cache_key, semantic_market_snapshot


def _key(snapshot):
    return build_review_cache_key(
        skill_hash="skill-v1",
        stock={"code": "000001", "price": 10.0},
        snapshot=snapshot,
        rule_signal={"score": 80},
        provider="openai-compatible",
        model="deepseek-chat",
        system_prompt="system",
        user_prompt=str(semantic_market_snapshot(snapshot)),
    )


def test_cache_key_ignores_runtime_snapshot_metadata():
    left = {"trade_date": "2026-07-24", "up_count": 3000, "snapshot_id": "a", "breadth": {"regime": "强势", "generated_at": "a"}}
    right = {"trade_date": "2026-07-24", "up_count": 3000, "snapshot_id": "b", "breadth": {"regime": "强势", "generated_at": "b", "quote_cache_age_seconds": 9}}
    assert _key(left) == _key(right)


def test_cache_key_changes_when_semantic_market_state_changes():
    left = {"trade_date": "2026-07-24", "up_count": 3000, "breadth": {"regime": "强势"}}
    right = {"trade_date": "2026-07-24", "up_count": 1000, "breadth": {"regime": "弱势"}}
    assert _key(left) != _key(right)
