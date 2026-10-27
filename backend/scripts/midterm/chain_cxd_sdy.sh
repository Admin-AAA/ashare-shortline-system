#!/bin/bash
# §311 → §312 串行：先跑完短线 CXD_h5，再跑中线 SDY_W_h20（单进程逐段）。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s311.sh"; bash "$D/run_s312.sh"
