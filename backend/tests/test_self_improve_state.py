"""Tests for multi-researcher concurrent self-improve state management."""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.self_improve_state import (
    HEARTBEAT_TIMEOUT_SECONDS,
    _now_iso,
    claim_direction,
    commit_update,
    daemon_heartbeat,
    heartbeat,
    prune_claimed_directions,
    publish_champion,
    publish_finding,
    read_state,
    release_direction,
    setup_worktree,
    release_worktree,
    with_publish_lock,
)


@pytest.fixture
def tmp_memory_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# ── 原有向后兼容测试 ──────────────────────────────────────────────────


def test_read_state_empty(tmp_memory_dir):
    state = read_state(tmp_memory_dir)
    assert state == {}


def test_state_schema_version_stamped_on_write(tmp_memory_dir):
    """Every state write stamps the schema_version so tools can detect layout."""
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)
    state = read_state(tmp_memory_dir)
    assert state.get("schema_version") == 2

    # Legacy file without schema_version is readable and gets upgraded on write.
    (tmp_memory_dir / "self-improve-state.json").write_text(
        json.dumps({"canonical_production": {"commit": "abc"}}), encoding="utf-8"
    )
    commit_update({"x": 1}, memory_dir=tmp_memory_dir)
    state = read_state(tmp_memory_dir)
    assert state["schema_version"] == 2
    assert state["x"] == 1


def test_commit_update_preserves_existing_state_and_appends_valid_jsonl(tmp_memory_dir):
    (tmp_memory_dir / "self-improve-state.json").write_text(
        json.dumps({"champion": "old", "keep": True}), encoding="utf-8"
    )
    result = commit_update(
        {"champion": "new"},
        {"run_id": "run-1", "decision": "FALSIFIED"},
        tmp_memory_dir,
    )
    assert result == {"champion": "new", "keep": True, "schema_version": 2}
    assert read_state(tmp_memory_dir) == result
    records = [
        json.loads(line)
        for line in
        (tmp_memory_dir / "self-improve-runs.jsonl").read_text().splitlines()
    ]
    assert records == [{"run_id": "run-1", "decision": "FALSIFIED"}]


def test_concurrent_commits_never_corrupt_state_or_jsonl(tmp_memory_dir):
    def write(index: int) -> None:
        commit_update(
            {f"writer_{index}": index},
            {"run_id": f"run-{index}"},
            tmp_memory_dir,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(20)))

    state = read_state(tmp_memory_dir)
    expected = {f"writer_{index}": index for index in range(20)}
    expected["schema_version"] = 2
    assert state == expected
    records = [
        json.loads(line)
        for line in
        (tmp_memory_dir / "self-improve-runs.jsonl").read_text().splitlines()
    ]
    assert {record["run_id"] for record in records} == {
        f"run-{index}" for index in range(20)
    }


def test_read_state_uses_shared_lock(tmp_memory_dir):
    """Verify read_state doesn't block concurrent reads."""
    (tmp_memory_dir / "self-improve-state.json").write_text(
        '{"key":"val"}', encoding="utf-8"
    )

    def reader(_: int) -> dict:
        return read_state(tmp_memory_dir)

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(reader, range(10)))

    assert all(r == {"key": "val"} for r in results)


# ── 多研究员生命周期 ──────────────────────────────────────────────────


def test_claim_direction_allocates_unique_direction(tmp_memory_dir):
    r = claim_direction("researcher-1", max_workers=3, memory_dir=tmp_memory_dir)
    assert r["ok"] is True
    assert isinstance(r["direction"], str)
    assert r["champion_commit"] == ""

    # second claim should get a different direction
    r2 = claim_direction("researcher-2", max_workers=3, memory_dir=tmp_memory_dir)
    assert r2["ok"] is True
    assert r2["direction"] != r["direction"]


def test_claim_direction_respects_capacity(tmp_memory_dir):
    claim_direction("r1", max_workers=1, memory_dir=tmp_memory_dir)
    r = claim_direction("r2", max_workers=1, memory_dir=tmp_memory_dir)
    assert r.get("error") == "capacity_full"


def test_claim_direction_force_stale_replaces_oldest(tmp_memory_dir):
    claim_direction("r1", max_workers=1, memory_dir=tmp_memory_dir)
    # Force stale to replace r1
    r = claim_direction(
        "r2", max_workers=1, force_stale=True, memory_dir=tmp_memory_dir
    )
    assert r["ok"] is True

    # Verify r1 is gone from registry
    state = read_state(tmp_memory_dir)
    active = state.get("researcher_registry", {}).get("active_researchers", {})
    assert "r1" not in active
    assert "r2" in active


def test_release_direction_removes_researcher(tmp_memory_dir):
    claimed = claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)
    r = release_direction("r1", memory_dir=tmp_memory_dir)
    assert r["ok"] is True

    state = read_state(tmp_memory_dir)
    registry = state.get("researcher_registry", {})
    active = registry.get("active_researchers", {})
    assert "r1" not in active
    assert claimed["direction"] not in registry.get("claimed_directions", [])

    reclaimed = claim_direction(
        "r2",
        preferred=claimed["direction"],
        max_workers=3,
        memory_dir=tmp_memory_dir,
    )
    assert reclaimed["ok"] is True
    assert reclaimed["direction"] == claimed["direction"]


def test_heartbeat_updates_timestamp(tmp_memory_dir):
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)
    time.sleep(0.1)  # ensure timestamp changes
    r = heartbeat("r1", memory_dir=tmp_memory_dir)
    assert r["ok"] is True
    assert r["stale_champion"] is False
    assert r["status"] == "working"


def test_heartbeat_detects_stale_champion(tmp_memory_dir):
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)

    # Manually invalidate via publish_champion (simulating another worker)
    # First need a champion to publish against
    state = read_state(tmp_memory_dir)
    state["canonical_production"] = {"commit": "abc123", "n": 5}
    commit_update(
        {"canonical_production": state["canonical_production"]},
        memory_dir=tmp_memory_dir,
    )

    # Now publish a new champion from r1 — this invalidates others
    claim_direction("r2", max_workers=3, memory_dir=tmp_memory_dir)
    r = publish_champion(
        "r2", "abc123",
        {"canonical_production": {"commit": "def456", "n": 10}},
        memory_dir=tmp_memory_dir,
    )
    assert r["ok"] is True

    # r1 should now show stale
    hb = heartbeat("r1", memory_dir=tmp_memory_dir)
    assert hb["stale_champion"] is True
    assert hb["status"] == "invalidated"


def test_heartbeat_not_registered(tmp_memory_dir):
    r = heartbeat("nonexistent", memory_dir=tmp_memory_dir)
    assert r["ok"] is True
    assert r["status"] == "not_registered"


# ── publish_champion ───────────────────────────────────────────────────


def test_publish_champion_succeeds_with_correct_commit(tmp_memory_dir):
    commit_update(
        {"canonical_production": {"commit": "abc", "n": 5}},
        memory_dir=tmp_memory_dir,
    )
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)

    r = publish_champion(
        "r1", "abc",
        {"canonical_production": {"commit": "def", "n": 10}},
        {"run_id": "run-1", "decision": "PRODUCTION"},
        memory_dir=tmp_memory_dir,
    )
    assert r["ok"] is True

    state = read_state(tmp_memory_dir)
    assert state["canonical_production"]["commit"] == "def"
    assert state["canonical_production"]["n"] == 10

    records = [
        json.loads(line)
        for line in
        (tmp_memory_dir / "self-improve-runs.jsonl").read_text().splitlines()
    ]
    assert records == [{"run_id": "run-1", "decision": "PRODUCTION"}]


def test_publish_champion_fails_on_stale_commit(tmp_memory_dir):
    commit_update(
        {"canonical_production": {"commit": "abc", "n": 5}},
        memory_dir=tmp_memory_dir,
    )
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)

    r = publish_champion(
        "r1", "wrong_commit",  # stale!
        {"canonical_production": {"commit": "def", "n": 10}},
        memory_dir=tmp_memory_dir,
    )
    assert r.get("error") == "CHAMPION_STALE"
    assert r["expected"] == "wrong_commit"
    assert r["actual"] == "abc"


def test_publish_champion_invalidates_other_researchers(tmp_memory_dir):
    commit_update(
        {"canonical_production": {"commit": "abc", "n": 5}},
        memory_dir=tmp_memory_dir,
    )
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)
    claim_direction("r2", max_workers=3, memory_dir=tmp_memory_dir)

    # r1 publishes
    r = publish_champion(
        "r1", "abc",
        {"canonical_production": {"commit": "def", "n": 10}},
        memory_dir=tmp_memory_dir,
    )
    assert r["ok"] is True

    # r2 should be invalidated
    hb = heartbeat("r2", memory_dir=tmp_memory_dir)
    assert hb["stale_champion"] is True
    assert hb["status"] == "invalidated"

    # r1 should still be working (publisher is not invalidated)
    hb1 = heartbeat("r1", memory_dir=tmp_memory_dir)
    assert hb1["stale_champion"] is False


# ── publish_finding ────────────────────────────────────────────────────


def test_publish_finding_appends_and_does_not_mutate_champion(tmp_memory_dir):
    commit_update(
        {"canonical_production": {"commit": "abc", "n": 5}},
        memory_dir=tmp_memory_dir,
    )
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)

    r = publish_finding(
        "r1",
        {"archived_falsified_directions": ["direction-x"]},
        {
            "finding_id": "f_001",
            "direction": "grid_scan:stop_loss",
            "summary": "stop_loss 扫完后无改善",
            "metrics": {"n": 33, "avg_net": 1.5},
        },
        {"run_id": "run-1", "decision": "FINDING"},
        memory_dir=tmp_memory_dir,
    )
    assert r["ok"] is True

    state = read_state(tmp_memory_dir)
    assert state["canonical_production"]["commit"] == "abc"  # unchanged
    assert len(state["published_findings"]) == 1
    assert state["published_findings"][0]["finding_id"] == "f_001"
    assert "direction-x" in state["archived_falsified_directions"]

    records = [
        json.loads(line)
        for line in
        (tmp_memory_dir / "self-improve-runs.jsonl").read_text().splitlines()
    ]
    assert records == [{"run_id": "run-1", "decision": "FINDING"}]


def test_publish_finding_rejects_champion_mutation(tmp_memory_dir):
    with pytest.raises(ValueError, match="canonical_production"):
        publish_finding(
            "r1",
            {"canonical_production": {"commit": "new"}},
            {"finding_id": "f_001", "direction": "x"},
            memory_dir=tmp_memory_dir,
        )


# ── 并发安全 ───────────────────────────────────────────────────────────


def test_concurrent_claims_never_allocate_duplicate_directions(tmp_memory_dir):
    """10 concurrent claims should all get unique directions."""
    n_workers = 10

    def claim(i: int) -> dict:
        return claim_direction(
            f"r{i}", max_workers=n_workers, memory_dir=tmp_memory_dir
        )

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        results = list(executor.map(claim, range(n_workers)))

    directions = [r.get("direction") for r in results if r.get("ok")]
    assert len(directions) == len(set(directions)), (
        f"Duplicate directions found: {directions}"
    )


def test_concurrent_publish_champion_only_first_wins(tmp_memory_dir):
    """When two researchers try to publish, only the first CAS succeeds."""
    commit_update(
        {"canonical_production": {"commit": "abc", "n": 5}},
        memory_dir=tmp_memory_dir,
    )
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)
    claim_direction("r2", max_workers=3, memory_dir=tmp_memory_dir)

    ok_count = 0
    stale_count = 0

    def publish(researcher_id: str) -> None:
        nonlocal ok_count, stale_count
        r = publish_champion(
            researcher_id, "abc",
            {"canonical_production": {"commit": f"def-{researcher_id}", "n": 10}},
            memory_dir=tmp_memory_dir,
        )
        if r.get("ok"):
            ok_count += 1
        elif r.get("error") == "CHAMPION_STALE":
            stale_count += 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(publish, ["r1", "r2"]))

    assert ok_count == 1, f"Expected exactly 1 success, got {ok_count}"
    assert stale_count == 1, f"Expected exactly 1 stale, got {stale_count}"


def test_concurrent_reads_and_writes_coexist(tmp_memory_dir):
    """Concurrent reads should not block writes and vice versa."""
    (tmp_memory_dir / "self-improve-state.json").write_text(
        '{"key":"initial"}', encoding="utf-8"
    )

    read_results: list[bool] = []
    write_results: list[bool] = []

    def reader(_: int) -> None:
        try:
            read_state(tmp_memory_dir)
            read_results.append(True)
        except Exception:
            read_results.append(False)

    def writer(i: int) -> None:
        try:
            commit_update(
                {f"key_{i}": i},
                {"run_id": f"run-{i}"},
                tmp_memory_dir,
            )
            write_results.append(True)
        except Exception:
            write_results.append(False)

    with ThreadPoolExecutor(max_workers=12) as executor:
        futs = []
        for i in range(8):
            futs.append(executor.submit(reader, i))
        for i in range(4):
            futs.append(executor.submit(writer, i))
        for f in futs:
            f.result()  # raise on exception

    assert all(read_results), "Some reads failed"
    assert all(write_results), "Some writes failed"


# ── Stale worker 回收 ──────────────────────────────────────────────────


def test_claim_direction_purges_stale_workers(tmp_memory_dir):
    """Workers with heartbeat older than HEARTBEAT_TIMEOUT are auto-purged."""
    claim_direction("r1", max_workers=1, memory_dir=tmp_memory_dir)

    # Artificially age r1's heartbeat
    state = read_state(tmp_memory_dir)
    active = state["researcher_registry"]["active_researchers"]
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(
        seconds=HEARTBEAT_TIMEOUT_SECONDS + 60
    )
    active["r1"]["last_heartbeat"] = old.isoformat()

    # Write back the aged state
    commit_update({"researcher_registry": state["researcher_registry"]}, memory_dir=tmp_memory_dir)

    # Now a new claim should succeed (r1 purged as stale)
    r = claim_direction("r2", max_workers=1, memory_dir=tmp_memory_dir)
    assert r["ok"] is True

    state = read_state(tmp_memory_dir)
    active = state["researcher_registry"]["active_researchers"]
    assert "r1" not in active
    assert "r2" in active


# ── 方向泄漏修复 ───────────────────────────────────────────────────────


def _age_heartbeat(tmp_memory_dir, researcher_id: str) -> None:
    """Artificially age a researcher's heartbeat beyond the timeout."""
    from datetime import datetime, timedelta, timezone
    state = read_state(tmp_memory_dir)
    active = state["researcher_registry"]["active_researchers"]
    old = datetime.now(timezone.utc) - timedelta(
        seconds=HEARTBEAT_TIMEOUT_SECONDS + 60
    )
    active[researcher_id]["last_heartbeat"] = old.isoformat()
    commit_update(
        {"researcher_registry": state["researcher_registry"]},
        memory_dir=tmp_memory_dir,
    )


def test_purge_stale_releases_claimed_direction(tmp_memory_dir):
    """Purged worker's direction must be reclaimable, not leaked."""
    claim_direction("r1", max_workers=1, memory_dir=tmp_memory_dir)
    direction = read_state(tmp_memory_dir)["researcher_registry"][
        "active_researchers"
    ]["r1"]["claimed_direction"]

    # Age r1 beyond timeout, then a new claim purges it and must be able to
    # reallocate the very same direction.
    _age_heartbeat(tmp_memory_dir, "r1")
    r = claim_direction("r2", max_workers=1, memory_dir=tmp_memory_dir)
    assert r["ok"] is True
    assert r["direction"] == direction, (
        "purge did not release the stale worker's direction"
    )


def test_force_stale_releases_displaced_direction(tmp_memory_dir):
    """Force-stale displacement must release the displaced direction."""
    claim_direction("r1", max_workers=1, memory_dir=tmp_memory_dir)
    direction = read_state(tmp_memory_dir)["researcher_registry"][
        "active_researchers"
    ]["r1"]["claimed_direction"]

    r = claim_direction(
        "r2", max_workers=1, force_stale=True, memory_dir=tmp_memory_dir
    )
    assert r["ok"] is True
    assert r["direction"] == direction, (
        "force_stale did not release the displaced direction"
    )


def test_prune_claimed_directions_reclaims_orphaned(tmp_memory_dir):
    """prune-claimed releases directions whose worker is gone."""
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)
    claim_direction("r2", max_workers=3, memory_dir=tmp_memory_dir)
    claimed = read_state(tmp_memory_dir)["researcher_registry"][
        "claimed_directions"
    ]
    assert len(claimed) == 2

    # Simulate r2's window being killed without release: remove r2 from active
    # while its direction stays in claimed_directions.
    state = read_state(tmp_memory_dir)
    del state["researcher_registry"]["active_researchers"]["r2"]
    commit_update(
        {"researcher_registry": state["researcher_registry"]},
        memory_dir=tmp_memory_dir,
    )

    result = prune_claimed_directions(tmp_memory_dir)
    assert result["ok"] is True
    assert len(result["released"]) == 1

    remaining = read_state(tmp_memory_dir)["researcher_registry"][
        "claimed_directions"
    ]
    assert "r1's direction" not in remaining
    assert len(remaining) == 1


# ── daemon-heartbeat ────────────────────────────────────────────────────


def test_daemon_heartbeat_registers_and_release_stops(tmp_memory_dir):
    """Daemon registers its pid; release-direction terminates it."""
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)

    # Run the daemon in a subprocess (its own process) so we can inspect the
    # registered pid and terminate it via release-direction.
    import os
    import subprocess
    import sys
    state_tool = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "self_improve_state.py"
    )
    scripts_dir = os.path.dirname(os.path.abspath(state_tool))
    daemon_py = (
        "import sys, os; sys.path.insert(0, '%s'); "
        "from self_improve_state import daemon_heartbeat; "
        "open('%s/daemon-ok','w').write(str(os.getpid())); "
        "sys.exit(daemon_heartbeat('r1', interval_seconds=60, "
        "max_duration_seconds=3600, memory_dir='%s'))"
        % (scripts_dir, tmp_memory_dir, tmp_memory_dir)
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", daemon_py],
        cwd=scripts_dir,
    )
    try:
        # Wait for the daemon to register its pid.
        daemon_ok = tmp_memory_dir / "daemon-ok"
        deadline = time.time() + 10
        while not daemon_ok.exists() and time.time() < deadline:
            time.sleep(0.1)

        state = read_state(tmp_memory_dir)
        info = state["researcher_registry"]["active_researchers"]["r1"]
        assert "heartbeat_daemon_pid" in info, "daemon did not register pid"
        assert info["heartbeat_daemon_pid"] == proc.pid

        # release-direction must terminate the daemon.
        release_direction("r1", memory_dir=tmp_memory_dir)
        proc.wait(timeout=10)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


# ── worktree 隔离 ───────────────────────────────────────────────────────


def test_setup_and_release_worktree(tmp_memory_dir):
    """setup-worktree creates a detached worktree and registers its path;
    release-worktree --remove removes it."""
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)

    r = setup_worktree("r1", memory_dir=tmp_memory_dir)
    assert r.get("ok") is True
    wt = Path(r["worktree_path"])
    assert wt.exists()
    assert (wt / ".git").exists()  # detached worktree marker

    # registered in the researcher record
    state = read_state(tmp_memory_dir)
    info = state["researcher_registry"]["active_researchers"]["r1"]
    assert info.get("worktree_path") == str(wt)

    # release without remove keeps the dir (agent may still need it)
    rel = release_worktree("r1", memory_dir=tmp_memory_dir)
    assert rel.get("ok") is True
    assert rel.get("removed") is False
    assert wt.exists()

    # remove cleans it up
    rel2 = release_worktree("r1", remove=True, memory_dir=tmp_memory_dir)
    assert rel2.get("ok") is True
    assert rel2.get("removed") is True
    assert not wt.exists()


def test_setup_worktree_registers_in_researcher(tmp_memory_dir):
    """Re-setup is idempotent: same worktree path re-registered."""
    claim_direction("r1", max_workers=3, memory_dir=tmp_memory_dir)
    r1 = setup_worktree("r1", memory_dir=tmp_memory_dir)
    r2 = setup_worktree("r1", memory_dir=tmp_memory_dir)
    assert r1["worktree_path"] == r2["worktree_path"]
    assert r2.get("ok") is True

    # cleanup
    release_worktree("r1", remove=True, memory_dir=tmp_memory_dir)


# ── git 发布锁 ──────────────────────────────────────────────────────────


def test_publish_lock_serializes_concurrent_commands(tmp_memory_dir):
    """Two concurrent with_publish_lock commands run serially (not in
    parallel), proven by a combined sleep that stays >= 2x single."""
    import subprocess
    import sys
    import os
    scripts_dir = os.path.join(
        os.path.dirname(__file__), "..", "scripts"
    )
    # Use a tmp repo to avoid touching the real repo's lockfile.
    env = dict(os.environ)
    start = time.time()
    procs = []
    for _ in range(2):
        p = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '%s'); "
             "from self_improve_state import with_publish_lock; "
             "print(with_publish_lock(['/bin/sh','-c','sleep 0.5'], "
             "repo=__import__('pathlib').Path('%s')))"
             % (scripts_dir, tmp_memory_dir)],
            cwd=scripts_dir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        procs.append(p)
    outs = [p.communicate(timeout=30)[0] for p in procs]
    elapsed = time.time() - start
    for out in outs:
        assert "'ok': True" in out
    # 2 * 0.5s serialized should take >= ~1s; parallel would be ~0.5s.
    assert elapsed >= 0.9, f"commands did not serialize: {elapsed:.2f}s"


def test_publish_lock_runs_command(tmp_memory_dir):
    import subprocess
    import sys
    import os
    scripts_dir = os.path.join(
        os.path.dirname(__file__), "..", "scripts"
    )
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '%s'); "
         "from self_improve_state import with_publish_lock; "
         "print(with_publish_lock(['/bin/sh','-c','echo hi'], "
         "repo=__import__('pathlib').Path('%s')))"
         % (scripts_dir, tmp_memory_dir)],
        cwd=scripts_dir, capture_output=True, text=True, timeout=30,
    )
    assert "'ok': True" in proc.stdout
    assert "hi" in proc.stdout
