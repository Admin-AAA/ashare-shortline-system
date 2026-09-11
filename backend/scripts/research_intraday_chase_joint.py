"""pm_ret × 信号日 pct_chg 联合否决精度快筛（只读，选中池缓存面板）。
目标：加"当日已上涨" conjunct 能否让被砍票更毒、两窗配对日 CI 更稳。"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"


def boot_ci(d, draws=4000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(d); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5])


def add_pct():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            eng = create_engine(line.split("=", 1)[1].strip().strip('"').strip("'")); break
    p = pd.read_pickle(OUT / "intraday_selected_panel.pkl")
    pr = p[["dt", "code"]].drop_duplicates()
    dk = pd.read_sql(text("""SELECT trade_date dt, code, pct_chg FROM daily_kbars
        WHERE trade_date BETWEEN :a AND :b AND code IN :c"""), eng,
        params={"a": pr.dt.min(), "b": pr.dt.max(), "c": tuple(sorted(pr.code.unique()))})
    dk["dt"] = dk.dt.astype(str); dk["code"] = dk.code.astype(str)
    return p.merge(dk, on=["dt", "code"], how="left")


def main():
    p = add_pct()
    fzd = sorted(pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl").dt.astype(str).unique())
    fwd = sorted(pd.read_pickle(OUT / "canon_forward_v2_selected.pkl").dt.astype(str).unique())
    CELLS = {"弱势·活跃", "强牛·高潮"}
    for Tpm in (1.0, 1.25, 1.5):
        for Tpc in (0.0, 2.0, 4.0):
            line = f"pm>{Tpm} & pct>{Tpc}: "
            for win, d, ad in [("fz", p[p.win == "frozen"], fzd), ("fw", p[p.win == "forward"], fwd)]:
                x = d.dropna(subset=["pm_ret", "pct_chg"]).copy()
                tox = x.key.isin(CELLS) & (x.pm_ret > Tpm) & (x.pct_chg > Tpc)
                cut, keep = x[tox], x[~tox]
                b = x.groupby("dt").net.mean().reindex(ad).fillna(0)
                n = keep.groupby("dt").net.mean().reindex(ad).fillna(0)
                touched = sorted(set(cut.dt)); chg = (n - b).loc[touched]; lo, hi = boot_ci(chg.values)
                line += (f"{win}砍{len(cut):>2}均{cut.net.mean():+.2f}/w{(cut.net>0).mean():.2f} "
                         f"日{b.mean():+.3f}->{n.mean():+.3f} CI[{lo:+.3f},{hi:+.3f}  ")
            print(line)


if __name__ == "__main__":
    main()
