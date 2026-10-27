#!/bin/bash
# §328 → §329 串行。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s328.sh"; bash "$D/run_s329.sh"
