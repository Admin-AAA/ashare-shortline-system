#!/usr/bin/env bash
set -euo pipefail

test "${SELF_IMPROVE_ISOLATED_RUN:-}" = "1"
test -n "${SELF_IMPROVE_RUN_ID:-}"
test "$(git branch --show-current)" = ""
test "$(git rev-parse HEAD)" = "$(git rev-parse main)"
test "$PWD" != "${ASHARE_ROOT}"
echo "$PWD" > "${MOCK_RESULT}"
