"""T7：LLM 审查层健康度可见性（北极星 B 项「LLM delta 缺失」可见性缺口）。

LLM 审查只在【成功】时写 llm_review_cache；key 失效 / provider 故障时审查静默停摆，
推荐缺 LLM delta 而用户无感知。本测试锁定：

1. 纯函数 _llm_health_status 的状态判定（ok / stale / never 各分支）。
2. 服务函数 llm_review_health 对真实 DB 的聚合（seeded 行 → status ok + 字段齐全）。
"""

from datetime import datetime, timedelta, timezone

from app.models.tables import LLMReviewCache
from app.services.llm_review_cache_service import (
    _llm_health_status,
    llm_review_health,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# 纯函数：状态判定
# --------------------------------------------------------------------------- #
def test_status_never_when_empty():
    assert _llm_health_status(None, 0, 0, _now()) == "never"


def test_status_never_when_no_last_at():
    assert _llm_health_status(None, 3, 10, _now()) == "never"


def test_status_ok_within_24h_with_recent():
    now = _now()
    last = now - timedelta(hours=23)
    assert _llm_health_status(last, 5, 10, now) == "ok"


def test_status_stale_when_older_than_24h():
    now = _now()
    last = now - timedelta(hours=30)
    assert _llm_health_status(last, 0, 10, now) == "stale"


def test_status_stale_when_no_recent_hits():
    # 边界：last_at 不到 24h，但近 24h 零命中（不应发生，但逻辑须判 stale）
    now = _now()
    last = now - timedelta(hours=1)
    assert _llm_health_status(last, 0, 10, now) == "stale"


def test_status_ok_exact_boundary_24h():
    now = _now()
    last = now - timedelta(hours=24)
    # age == 24h（不严格 > 阈值），且 recent>0 → ok。
    # 注意：必须用同一个 now，否则两次 _now() 微秒差会让 age 略 > 24h。
    assert _llm_health_status(last, 2, 10, now) == "ok"


def test_status_stale_just_over_24h():
    now = _now()
    last = now - timedelta(hours=24, seconds=1)
    # age 严格 > 24h → stale（即便 recent>0，也以「最近成功」为准）
    assert _llm_health_status(last, 2, 10, now) == "stale"


# --------------------------------------------------------------------------- #
# 服务函数：真实 DB 聚合（seeded 行）
# --------------------------------------------------------------------------- #
def test_service_returns_ok_with_seeded_row(db):
    now = _now()
    row = LLMReviewCache(
        cache_key="__test_health_key__",
        skill_id="daily-stock-analysis-skill",
        skill_hash="h123",
        trade_date=now.strftime("%Y-%m-%d"),
        code="600000",
        review_json='{"delta_score": 1.2, "verdict": "ok"}',
        model="gpt-5.4",
    )
    db.add(row)
    db.commit()

    health = llm_review_health(db)

    assert health["status"] == "ok"
    assert health["total_entries"] >= 1
    assert health["recent_count_24h"] >= 1
    assert health["last_review_at"] is not None
    assert health["model"] == "gpt-5.4"
    assert health["checked_at"] is not None
