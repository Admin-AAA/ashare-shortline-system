#!/bin/bash
# §313 → §314 串行：先跑完短线 SFB_h5，再跑中线 SPS_W_h20（单进程逐段）。
D="$(cd "$(dirname "$0")" && pwd)"
bash "$D/run_s313.sh"; bash "$D/run_s314.sh"
