from pathlib import Path

from scripts.check_governance_store import check_store


def test_governance_store_accepts_content_addressed_objects(tmp_path: Path):
    store = tmp_path / "store"
    kind = store / "evidence"
    kind.mkdir(parents=True)
    (kind / ("a" * 64 + ".json")).write_text('{"kind":"evidence"}', encoding="utf-8")
    (store / "events.jsonl").write_text('{"event":"created"}\n', encoding="utf-8")

    assert check_store(store) == []


def test_governance_store_rejects_untracked_shape_and_invalid_json(tmp_path: Path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "temporary.json").write_text("not-json", encoding="utf-8")
    (store / "notes.txt").write_text("do not commit", encoding="utf-8")

    errors = check_store(store)

    assert any("invalid object path/name" in error for error in errors)
    assert any("unexpected governance store file" in error for error in errors)
