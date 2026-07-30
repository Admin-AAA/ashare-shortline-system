#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

REPO="$TEST_ROOT/repo"
mkdir -p "$REPO/backend/logs" "$REPO/scripts"
git -C "$REPO" init -q -b main
git -C "$REPO" config user.email test@example.com
git -C "$REPO" config user.name test
echo fixture > "$REPO/fixture.txt"
git -C "$REPO" add fixture.txt
git -C "$REPO" commit -q -m fixture

MOCK="$SCRIPT_DIR/test-fixtures/mock-self-improve-claude.sh"

MOCK_RESULT="$TEST_ROOT/result" \
ASHARE_ROOT="$REPO" \
SELF_IMPROVE_CLAUDE_BIN="$MOCK" \
SELF_IMPROVE_LOG="$TEST_ROOT/run.log" \
SELF_IMPROVE_LOCK="$TEST_ROOT/run.lock" \
SELF_IMPROVE_RUNS_DIR="$TEST_ROOT/runs" \
"$SCRIPT_DIR/run-self-improve.sh"

test -s "$TEST_ROOT/result"
test ! -e "$TEST_ROOT/run.lock"
test -z "$(git -C "$REPO" worktree list --porcelain | rg '^worktree ' | tail -n +2)"
echo "run-self-improve isolation test passed"
