import pytest

from app.services.validation.safety import (
    AcceptanceMode,
    assert_destructive_allowed,
    build_batch_id,
    tag_acceptance_name,
)


def test_build_batch_id_is_identifiable():
    batch_id = build_batch_id()
    assert batch_id.startswith("acceptance-")
    assert len(batch_id) >= len("acceptance-20260525-120000")


def test_tag_acceptance_name_contains_batch_id():
    tagged = tag_acceptance_name("驾驶舱验收", "acceptance-20260525-120000")
    assert tagged == "驾驶舱验收 [acceptance-20260525-120000]"


def test_acceptance_mode_requires_execute_for_writes():
    mode = AcceptanceMode(dry_run=True, execute=False)
    assert mode.can_write is False
    mode = AcceptanceMode(dry_run=False, execute=True)
    assert mode.can_write is True


def test_destructive_guard_blocks_without_double_confirm(monkeypatch):
    monkeypatch.delenv("ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE", raising=False)
    with pytest.raises(RuntimeError, match="destructive operation blocked"):
        assert_destructive_allowed(database_url="mysql+pymysql://user@prod/ashare_workbench", requested=True)


def test_destructive_guard_allows_non_production_when_requested(monkeypatch):
    monkeypatch.delenv("ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE", raising=False)
    assert_destructive_allowed(database_url="mysql+pymysql://user@127.0.0.1/ashare_test", requested=True)
