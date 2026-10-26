"""刚启动、缓存全空时，每个复盘页第一次打开的整页耗时（最坏情况：等同数据刚更新后的第一次）。
用法：python3 first_hit.py http://127.0.0.1:PORT"""
import sys
import time

import bench

bench.BASE = sys.argv[1].rstrip("/")
for name, fn in bench.PAGES.items():
    t = time.perf_counter()
    fn()
    print(f"{name} 首次 {1000 * (time.perf_counter() - t):.0f}ms")
