#!/bin/bash
# §316 → §317 串行：先跑完短线 WDB_h5，再跑中线 BDG_W_h20（单进程逐段；等待 §315 等其他研究进程结束、swap 空闲≥400MB）。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s316.sh"; bash "$D/run_s317.sh"
