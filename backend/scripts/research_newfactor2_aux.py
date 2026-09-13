"""新维度因子双窗口全市场筛（龙虎榜/两融/集合竞价），只读。

复用 research_newfactor_fullmarket.pkl 的全市场面板（含 T+1open→T+5close 毛 fwd5、
tradable_univ），从 DB 合并三张回填后跨双窗的辅助表，构造 T 时刻已知（strictly-prior）
因子，做日截面五分位 + Q5-Q1 逐月同号 + Spearman，两窗口（冻结/前瞻）对照。
毛 fwd5 仅用于快速杀因子；幸存者再上 canonical 因果 harness。

  龙虎榜 dragon_tiger（稀疏事件）：上榜 dummy、净买入 net_amount_yi、买卖比
  两融 margin_detail：当日融资买入 rzmre（杠杆热情）、rzye 环比变化、融券 rqyl
  竞价 auction_data：竞价 amount 截面分位、竞价量比 volume_ratio、竞价换手、竞价跳空
"""
from __future__ import annotations
import sys, pickle
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
FZ = ("2025-01-15", "2026-01-16"); FW = ("2026-02-09", "2026-08-05")


def db_url():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no db url")


def load_aux():
    eng = create_engine(db_url())
    out = {}
    with eng.connect() as c:
        out["dt"] = pd.read_sql(text(
            "SELECT code,trade_date,net_amount_yi,buy_amount_yi,sell_amount_yi FROM dragon_tiger"), c)
        out["mg"] = pd.read_sql(text(
            "SELECT code,trade_date,rzye,rzmre,rzche,rqyl FROM margin_detail"), c)
        out["au"] = pd.read_sql(text(
            "SELECT code,trade_date,vol,price,amount,pre_close,turnover_rate,volume_ratio FROM auction_data"), c)
    return out


def quant_screen(df, col, nm, q=5, event_only=False):
    print(f"\n===== {nm} ({col}) 日截面{q}分位, T+1open→T+5close 毛均收益% =====")
    for wname, (lo, hi) in [("冻结", FZ), ("前瞻", FW)]:
        x = df[(df.trade_date >= lo) & (df.trade_date <= hi)].copy()
        if event_only:
            x = x[x[col].notna()]
        else:
            x = x[x[col].notna()]
        if len(x) < 500:
            print(f"  {wname}: 样本不足 n={len(x)}"); continue
        x["q"] = x.groupby("trade_date")[col].rank(pct=True)
        try:
            x["bin"] = pd.qcut(x["q"], q, labels=False, duplicates="drop")
        except Exception:
            x["bin"] = (x["q"] * q).clip(upper=q - 1).astype(int)
        means = x.groupby("bin", observed=True).fwd5.mean()
        q_lo = x[x.q <= 1 / q].fwd5.mean(); q_hi = x[x.q > 1 - 1 / q].fwd5.mean()
        g = x.groupby("trade_date").apply(
            lambda z: z[z.q > 1 - 1 / q].fwd5.mean() - z[z.q <= 1 / q].fwd5.mean(),
            include_groups=False).dropna()
        msame = g.groupby(g.index.str[:7]).mean().pipe(lambda s: (np.sign(s) > 0).mean())
        rho = x[[col, "fwd5"]].corr(method="spearman").iloc[0, 1]
        grad = " ".join(f"Q{i}={means.get(i, float('nan')):+.3f}" for i in means.index)
        print(f"  {wname} n={len(x)}: {grad} | Qhi-Qlo={q_hi-q_lo:+.4f} 逐月同号{msame:.2f} rho={rho:+.4f}")


def event_vs_rest(df, flagcol, nm):
    print(f"\n===== {nm}: 事件 vs 非事件 =====")
    for wname, (lo, hi) in [("冻结", FZ), ("前瞻", FW)]:
        x = df[(df.trade_date >= lo) & (df.trade_date <= hi)]
        a = x[x[flagcol] == 1].fwd5; b = x[x[flagcol] != 1].fwd5
        g = x.groupby("trade_date").apply(
            lambda z: z[z[flagcol] == 1].fwd5.mean() - z[z[flagcol] != 1].fwd5.mean(),
            include_groups=False).dropna()
        ms = g.groupby(g.index.str[:7]).mean().pipe(lambda s: (np.sign(s) > 0).mean())
        print(f"  {wname}: 事件 n{len(a)} 均{a.mean():+.3f}/胜{(a>0).mean():.3f} | "
              f"非事件 n{len(b)} 均{b.mean():+.3f} | 日差{g.mean():+.4f} 逐月同号{ms:.2f}")


def main():
    panel = pickle.load(open(OUT / "newfactor_fullmarket.pkl", "rb"))
    d = panel[panel.fwd5.notna() & (panel.tradable_univ == True)][
        ["trade_date", "code", "fwd5"]].copy()
    aux = load_aux()
    # ---- dragon tiger ----
    dt = aux["dt"].copy(); dt["on_lhb"] = 1
    dt["bs_ratio"] = (dt.buy_amount_yi / dt.sell_amount_yi.replace(0, np.nan))
    d = d.merge(dt[["code", "trade_date", "on_lhb", "net_amount_yi", "bs_ratio"]],
                on=["code", "trade_date"], how="left")
    # ---- margin ----
    mg = aux["mg"].copy()
    mg = mg.sort_values(["code", "trade_date"])
    mg["rzye_chg"] = mg.groupby("code").rzye.pct_change()
    mg["rzmre_norm"] = mg.rzmre / mg.rzye.replace(0, np.nan)  # 当日融资买入/融资余额
    d = d.merge(mg[["code", "trade_date", "rzmre", "rzmre_norm", "rzye_chg", "rqyl"]],
                on=["code", "trade_date"], how="left")
    d["on_margin"] = np.where(d.rzmre.notna(), 1, 0)
    # ---- auction ----
    au = aux["au"].copy()
    au["auc_gap"] = au.price / au.pre_close - 1
    # 竞价额取 log，截面排名（绝对值受市值影响，靠日截面 rank 去趋势）
    au["log_auc_amt"] = np.log1p(au.amount.clip(lower=0))
    d = d.merge(au[["code", "trade_date", "log_auc_amt", "volume_ratio",
                    "turnover_rate", "auc_gap"]].rename(
        columns={"turnover_rate": "auc_turn"}), on=["code", "trade_date"], how="left")
    d["has_auc"] = np.where(d.log_auc_amt.notna(), 1, 0)
    print("merged panel:", d.shape, "lhb rows", d.on_lhb.notna().sum(),
          "margin", (d.on_margin == 1).sum(), "auc", (d.has_auc == 1).sum())

    event_vs_rest(d, "on_lhb", "龙虎榜上榜")
    quant_screen(d, "net_amount_yi", "龙虎榜净买入额(亿)[仅上榜]", event_only=True)
    quant_screen(d, "bs_ratio", "龙虎榜买卖比[仅上榜]", event_only=True)
    event_vs_rest(d, "on_margin", "两融标的")
    quant_screen(d, "rzmre", "当日融资买入额 rzmre")
    quant_screen(d, "rzmre_norm", "融资买入/余额 rzmre_norm")
    quant_screen(d, "rzye_chg", "融资余额环比 rzye_chg")
    event_vs_rest(d, "has_auc", "有竞价数据")
    quant_screen(d, "log_auc_amt", "竞价成交额(log)")
    quant_screen(d, "volume_ratio", "竞价量比 volume_ratio")
    quant_screen(d, "auc_turn", "竞价换手率")
    quant_screen(d, "auc_gap", "竞价跳空 auc_gap")
    d.to_pickle(OUT / "newfactor2_aux_panel.pkl")
    print("\nsaved panel -> newfactor2_aux_panel.pkl")


if __name__ == "__main__":
    main()
