#!/bin/bash
# §336 → §337 串行。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s336.sh"; bash "$D/run_s337.sh"
