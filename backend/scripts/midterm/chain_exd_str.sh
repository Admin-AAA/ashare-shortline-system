#!/bin/bash
# §323 → §324 串行。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s323.sh"; bash "$D/run_s324.sh"
