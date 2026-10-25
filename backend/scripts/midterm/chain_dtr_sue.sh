#!/bin/bash
# §318 → §319 串行：先跑完短线 DTR_h5，再跑中线 SUE_h20（单进程逐段；等待其他研究进程结束、swap 空闲≥400MB）。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s318.sh"; bash "$D/run_s319.sh"
