#!/bin/bash
# §321 → §322 串行。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s321.sh"; bash "$D/run_s322.sh"
