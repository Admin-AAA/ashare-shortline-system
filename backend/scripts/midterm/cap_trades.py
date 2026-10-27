"""§282 逐笔捕获包装器：不改任何研究脚本/引擎的规则，只在外面拦截 realistic.run_real / metrics_real 的返回。
用法：CAP_DIR=<目录> CAP_TAG=<r25|r16..> python cap_trades.py <原脚本.py> "<phase>" <原 out.json>
- run_real 系脚本：每次 run_real 调用后把逐笔表存成 CAP_DIR/<tag>__<sig>_h<max_hold>__<ph>.parquet，并记录引擎自己的 metrics_real(n/avg/pf/mdd)。
- lt_research16/19（自带 run）：拦截 metrics_real，当第 1 个参数就是调用者局部变量 t（策略本身那次，不是 20 次对照）时，按局部变量 nm 存。
"""
import sys, os, json, runpy, sys
import polars as pl
import realistic

script, ph, out = sys.argv[1], sys.argv[2], sys.argv[3]
cap_dir = os.environ["CAP_DIR"]; tag = os.environ["CAP_TAG"]
os.makedirs(cap_dir, exist_ok=True)
phs = ph.split()[0]
_run, _met = realistic.run_real, realistic.metrics_real
LOG = {}


def _save(name, t, m):
    if t is None or getattr(t, "height", 0) == 0:
        return
    t.write_parquet(os.path.join(cap_dir, f"{tag}__{name}__{phs}.parquet"))
    LOG[f"{name}__{phs}"] = {k: float(v) for k, v in m.items()}
    json.dump(LOG, open(os.path.join(cap_dir, f"{tag}__{phs}__engine.json"), "w"), indent=1)


def run_real(df, sig, sc, hold, s, e, *a, **kw):
    t, cur = _run(df, sig, sc, hold, s, e, *a, **kw)
    _save(f"{sig}_h{kw.get('max_hold')}", t, _met(t, cur))
    return t, cur


def metrics_real(t, cur, *a, **kw):
    m = _met(t, cur, *a, **kw)
    f = sys._getframe(1).f_locals
    if f.get("t") is t and "nm" in f and t is not None:
        _save(str(f["nm"]), t, m)
    return m


realistic.run_real = run_real
realistic.metrics_real = metrics_real
sys.argv = [script, ph, out]
runpy.run_path(script, run_name="__main__")
