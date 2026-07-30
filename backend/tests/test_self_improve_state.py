from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from scripts.self_improve_state import commit_update, read_state


def test_commit_update_preserves_existing_state_and_appends_valid_jsonl(tmp_path):
    (tmp_path / "self-improve-state.json").write_text(
        json.dumps({"champion": "old", "keep": True}), encoding="utf-8"
    )

    result = commit_update(
        {"champion": "new"}, {"run_id": "run-1", "decision": "FALSIFIED"}, tmp_path
    )

    assert result == {"champion": "new", "keep": True}
    assert read_state(tmp_path) == result
    records = [
        json.loads(line)
        for line in (tmp_path / "self-improve-runs.jsonl").read_text().splitlines()
    ]
    assert records == [{"run_id": "run-1", "decision": "FALSIFIED"}]


def test_concurrent_commits_never_corrupt_state_or_jsonl(tmp_path):
    def write(index: int) -> None:
        commit_update(
            {f"writer_{index}": index},
            {"run_id": f"run-{index}"},
            tmp_path,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(20)))

    state = read_state(tmp_path)
    assert state == {f"writer_{index}": index for index in range(20)}
    records = [
        json.loads(line)
        for line in (tmp_path / "self-improve-runs.jsonl").read_text().splitlines()
    ]
    assert {record["run_id"] for record in records} == {
        f"run-{index}" for index in range(20)
    }
